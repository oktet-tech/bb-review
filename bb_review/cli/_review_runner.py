"""Shared review orchestration for agent-based reviewers (OpenCode, Claude Code)."""

from datetime import datetime
import json
import logging
from pathlib import Path
import sys

import click

from ..git import RepoManagerError
from ..models import ReviewComment, ReviewFocus, ReviewResult, Severity
from ..reviewers import ParsedReview, extract_changed_files, parse_opencode_output
from ..rr import (
    ChainError,
    ReviewBoardClient,
    ReviewChain,
    ReviewFormatter,
    load_chain_from_file,
    resolve_chain,
)
from ..rr.chain import ChainedReview
from ._session import ReviewSession


logger = logging.getLogger(__name__)


def generate_branch_name(target_rr_id: int) -> str:
    """Generate a unique branch name for chain review."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"bb_review_{target_rr_id}_{timestamp}"


def create_mock_review_output(review_id: int) -> str:
    """Create mock review output for --fake-review mode."""
    return f"""### Issue: [MOCK] Critical Security Vulnerability
**File:** src/auth/login.c
**Line:** 127
**Severity:** critical
**Type:** security

[MOCK] Potential buffer overflow in authentication handler. User input is copied
to a fixed-size buffer without bounds checking, which could allow remote code
execution.

**Suggestion:** Use strncpy() with proper size limits or switch to a safer string handling library.

---

### Issue: [MOCK] Memory Leak in Error Path
**File:** src/network/socket.c
**Line:** 256
**Severity:** high
**Type:** bugs

[MOCK] Memory allocated for the connection context is not freed when the
connection fails during handshake. This can lead to memory exhaustion
under heavy load.

**Suggestion:** Add proper cleanup in the error handling block before returning.

---

### Issue: [MOCK] Inefficient Loop Pattern
**File:** src/data/parser.c
**Line:** 89
**Severity:** medium
**Type:** performance

[MOCK] Nested loops with O(n^2) complexity for data parsing. Consider using
a hash map for lookups to improve performance with large datasets.

**Suggestion:** Replace the inner loop with a hash table lookup.

---

### Issue: [MOCK] Inconsistent Naming Convention
**File:** src/utils/helpers.c
**Line:** 34
**Severity:** low
**Type:** style

[MOCK] Function name 'getData' uses camelCase while the rest of the codebase
uses snake_case. This inconsistency can make the code harder to maintain.

**Suggestion:** Rename to 'get_data' to match the project's coding style.

---

### Issue: [MOCK] Missing Error Handling
**File:** src/config/loader.c
**Line:** 178
**Severity:** high
**Type:** bugs

[MOCK] The return value of fopen() is not checked before use. If the config
file doesn't exist, this will cause a segmentation fault.

**Suggestion:** Add a NULL check and proper error reporting.

---

**Summary:** [MOCK REVIEW] This is a fake review generated for r/{review_id} for testing \
purposes. Found 5 issues: 1 critical, 2 high, 1 medium, 1 low severity.
"""


def build_submission_data(
    review_id: int,
    analysis: str,
    parsed,
    model: str | None,
    rr_summary: str | None = None,
    method_label: str = "OpenCode",
) -> dict:
    """Build submission data from parsed review output."""
    rb_comments = []
    general_issues = []

    for issue in parsed.issues:
        if issue.file_path and issue.line_number:
            text_parts = [f"**{issue.title}**"]
            if issue.severity:
                text_parts.append(f"Severity: {issue.severity}")
            if issue.comment:
                text_parts.append(issue.comment)
            if issue.suggestion:
                text_parts.append(f"\n**Suggestion:** {issue.suggestion}")

            rb_comments.append(
                {
                    "file_path": issue.file_path,
                    "line_number": issue.line_number,
                    "text": "\n".join(text_parts),
                }
            )
        else:
            general_issues.append(issue)

    body_parts = [f"**AI Review ({method_label})**\n"]

    if general_issues:
        body_parts.append("## General Issues\n")
        for issue in general_issues:
            body_parts.append(f"### {issue.title}")
            if issue.file_path:
                body_parts.append(f"**File:** `{issue.file_path}`")
            if issue.severity:
                body_parts.append(f"**Severity:** {issue.severity}")
            if issue.comment:
                body_parts.append(issue.comment)
            if issue.suggestion:
                body_parts.append(f"**Suggestion:** {issue.suggestion}")
            body_parts.append("")

    if parsed.unparsed_text:
        body_parts.append("## Additional Notes\n")
        body_parts.append(parsed.unparsed_text)

    if parsed.summary:
        body_parts.append(f"\n## Summary\n{parsed.summary}")

    body_top = "\n".join(body_parts)

    parsed_issues = [
        {
            "title": issue.title,
            "file_path": issue.file_path,
            "line_number": issue.line_number,
            "severity": issue.severity,
            "issue_type": issue.issue_type,
            "comment": issue.comment,
            "suggestion": issue.suggestion,
        }
        for issue in parsed.issues
    ]

    return ReviewFormatter.format_for_submission(
        review_request_id=review_id,
        body_top=body_top,
        comments=rb_comments,
        ship_it=False,
        unparsed_text=parsed.unparsed_text or "",
        parsed_issues=parsed_issues,
        metadata={
            "created_at": datetime.now().isoformat(),
            "model": model or "default",
            method_label.lower().replace(" ", "_"): True,
        },
        rr_summary=rr_summary,
    )


def save_to_review_db(
    config,
    review_id: int,
    diff_revision: int,
    repository: str,
    parsed,
    model: str,
    analysis_method: str = "opencode",
    rr_summary: str | None = None,
    chain_id: str | None = None,
    chain_position: int | None = None,
    fake: bool = False,
    body_top: str | None = None,
) -> None:
    """Save a review result to the reviews database."""
    from ..db import ReviewDatabase
    from ..rr.rb_client import ReviewRequestInfo

    comments = []
    has_critical = False

    for issue in parsed.issues:
        severity = Severity.MEDIUM
        if issue.severity:
            sev_lower = issue.severity.lower()
            if "critical" in sev_lower:
                severity = Severity.CRITICAL
                has_critical = True
            elif "high" in sev_lower:
                severity = Severity.HIGH
            elif "low" in sev_lower:
                severity = Severity.LOW

        issue_type = ReviewFocus.BUGS
        if issue.issue_type:
            type_lower = issue.issue_type.lower()
            if "security" in type_lower:
                issue_type = ReviewFocus.SECURITY
            elif "performance" in type_lower or "perf" in type_lower:
                issue_type = ReviewFocus.PERFORMANCE
            elif "style" in type_lower:
                issue_type = ReviewFocus.STYLE
            elif "architecture" in type_lower or "design" in type_lower:
                issue_type = ReviewFocus.ARCHITECTURE

        if issue.file_path and issue.line_number:
            comments.append(
                ReviewComment(
                    file_path=issue.file_path,
                    line_number=issue.line_number,
                    message=issue.comment or issue.title or "",
                    severity=severity,
                    issue_type=issue_type,
                    suggestion=issue.suggestion,
                )
            )

    result = ReviewResult(
        review_request_id=review_id,
        diff_revision=diff_revision,
        comments=comments,
        summary=parsed.summary or f"{analysis_method} analysis complete",
        has_critical_issues=has_critical,
    )

    rr_info = None
    if rr_summary:
        rr_info = ReviewRequestInfo(
            id=review_id,
            summary=rr_summary,
            status="pending",
            repository_name=repository,
            depends_on=[],
            base_commit_id=None,
            diff_revision=diff_revision,
        )

    rb_url = f"{config.reviewboard.url.rstrip('/')}/r/{review_id}/"

    try:
        review_db = ReviewDatabase(config.review_db.resolved_path)

        if chain_id:
            review_db.ensure_chain_exists(chain_id, repository, branch_name=chain_id)

        analysis_id = review_db.save_analysis(
            result=result,
            repository=repository,
            analysis_method=analysis_method,
            model=model,
            rr_info=rr_info,
            chain_id=chain_id,
            chain_position=chain_position,
            fake=fake,
            rb_url=rb_url,
            body_top=body_top,
        )
        logger.debug(f"Saved {analysis_method} analysis {analysis_id} to reviews database")
    except Exception as e:
        logger.warning(f"Failed to save to reviews database: {e}")


def run_review_command(
    session: ReviewSession,
    review_id: int,
    timeout: int,
    dry_run: bool,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    fallback: bool,
    chain: bool,
    chain_file: Path | None,
    base_commit: str | None,
    keep_branch: bool,
    review_from: int | None,
    series: bool = False,
) -> None:
    """Run the full review orchestration: chain resolution, checkout, review, save.

    Args:
        session: Shared review context (config, clients, callbacks, etc.).
        review_id: Target review request ID.
        timeout: Timeout in seconds.
        dry_run: If True, show what would be done without running reviews.
        dump_response: Path to dump raw response.
        output: Explicit output path.
        auto_output: Auto-generate output filenames.
        fallback: Allow fallback mode if patch doesn't apply.
        chain: Whether to resolve the dependency chain.
        chain_file: Manual chain file path.
        base_commit: Base commit override for chain_file.
        keep_branch: Keep the review branch after completion.
        review_from: Start reviewing from this RR ID.
        series: If True, review the entire chain as one unit.
    """
    # Validate series flag combinations
    if series:
        if not chain:
            raise click.UsageError("--series requires --chain (cannot use with --no-chain)")
        if review_from is not None:
            raise click.UsageError("--series is incompatible with --review-from")
        if session.series_reviewer_fn is None:
            raise click.UsageError("--series requires a series reviewer function")

    try:
        rb_client = session.rb_client
        repo_manager = session.repo_manager

        # Resolve chain
        if chain_file:
            click.echo(f"Loading chain from file: {chain_file}")
            review_chain = load_chain_from_file(rb_client, str(chain_file), base_commit)
        elif chain:
            click.echo("Resolving review chain...")

            def find_commit(repo_name: str, summary: str) -> str | None:
                repo_config = repo_manager.get_repo_by_rb_name(repo_name)
                if repo_config:
                    return repo_manager.find_commit_by_summary(repo_config.name, summary)
                return None

            review_chain = resolve_chain(rb_client, review_id, find_commit)
        else:
            rr_info = rb_client.get_review_request_info(review_id)
            review_chain = ReviewChain(
                repository=rr_info.repository_name,
                base_commit=rr_info.base_commit_id,
            )
            review_chain.reviews.append(
                ChainedReview(
                    review_request_id=review_id,
                    summary=rr_info.summary,
                    status=rr_info.status,
                    diff_revision=rr_info.diff_revision,
                    description=rr_info.description,
                    base_commit_id=rr_info.base_commit_id,
                    needs_review=True,
                )
            )

        # Apply --review-from filter
        if review_from is not None:
            chain_ids = [r.review_request_id for r in review_chain.reviews]
            if review_from not in chain_ids:
                raise click.ClickException(
                    f"Review r/{review_from} is not in the chain: {chain_ids}. "
                    f"Use one of the reviews in the chain."
                )
            found_start = False
            for review in review_chain.reviews:
                if review.review_request_id == review_from:
                    found_start = True
                if not found_start:
                    review.needs_review = False
                    click.echo(f"  Skipping review of r/{review.review_request_id} (context only)")

        pending = review_chain.pending_reviews
        if len(pending) == 0:
            click.echo("No pending reviews to analyze in chain.")
            return

        chain_str = " -> ".join(f"r/{r.review_request_id}" for r in review_chain.reviews)
        click.echo(f"  Chain: {chain_str}")
        click.echo(f"  To review: {len(pending)} patch(es)")
        click.echo(f"  Base commit: {review_chain.base_commit or 'default branch'}")

        # Validate repo config matches chain (session.repo_config was set by caller,
        # but verify it matches the resolved chain)
        repo_config = repo_manager.get_repo_by_rb_name(review_chain.repository)
        if repo_config is None:
            raise click.ClickException(
                f"Repository not configured: {review_chain.repository}. "
                "Add it to config.yaml under 'repositories'."
            )

        # Dry run
        if dry_run:
            _run_dry(
                rb_client,
                review_id,
                pending,
                session.model,
                session.method_label,
                keep_branch,
            )
            return

        # Series review: apply all patches, review once
        if series:
            _run_series_review(
                session, review_id, review_chain, repo_config, dump_response, output, auto_output, keep_branch
            )
            return

        # Single review
        if len(pending) == 1:
            _run_single_review(
                session, pending[0], repo_config, timeout, dump_response, output, auto_output, fallback
            )
            return

        # Chain review
        _run_chain_review(
            session,
            review_id,
            review_chain,
            pending,
            repo_config,
            timeout,
            dump_response,
            auto_output,
            fallback,
            keep_branch,
        )

    except ChainError as e:
        click.echo(f"Chain resolution error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Failed to analyze review with {session.method_label}")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _run_dry(
    rb_client: ReviewBoardClient,
    review_id: int,
    pending: list,
    model: str | None,
    method_label: str,
    keep_branch: bool,
) -> None:
    """Show what would be analyzed without running reviews."""
    click.echo("\n[DRY RUN] Would perform the following:")
    branch_name = generate_branch_name(review_id)
    click.echo(f"  [DRY RUN] Create branch: {branch_name}")

    for i, review in enumerate(pending):
        diff_info = rb_client.get_diff(review.review_request_id, review.diff_revision)
        files = extract_changed_files(diff_info.raw_diff)
        click.echo(f"  [DRY RUN] Review r/{review.review_request_id} ({i + 1}/{len(pending)}):")
        click.echo(f"    Summary: {review.summary[:60]}...")
        click.echo(f"    Files: {len(files)} changed")
        for f in files[:5]:
            click.echo(f"      - {f['path']}")
        if len(files) > 5:
            click.echo(f"      ... and {len(files) - 5} more")
        click.echo(f"    Would run {method_label} with model: {model or 'default'}")

    if keep_branch:
        click.echo(f"  [DRY RUN] Keep branch: {branch_name}")
    else:
        click.echo(f"  [DRY RUN] Delete branch: {branch_name}")

    click.echo("\nDry run complete. Use without --dry-run to perform actual review.")


def _run_single_review(
    session: ReviewSession,
    review,
    repo_config,
    timeout: int,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    fallback: bool,
) -> None:
    """Run review for a single review request using checkout_context."""
    review_id = review.review_request_id
    diff_info = session.rb_client.get_diff(review_id, review.diff_revision)
    raw_diff = diff_info.raw_diff

    click.echo(f"  Repository: {repo_config.name}")
    if diff_info.target_commit_id:
        click.echo(f"  Target commit: {diff_info.target_commit_id[:12]}")
    click.echo(f"  Base commit: {review.base_commit_id or 'default branch'}")

    try:
        with session.repo_manager.checkout_context(
            repo_config.name,
            base_commit=review.base_commit_id,
            branch=None,
            target_commit=diff_info.target_commit_id,
            patch=raw_diff,
            require_patch=not fallback,
        ) as (repo_path, used_target):
            if used_target:
                click.echo("  Checked out to reviewed state")
            elif fallback and not used_target:
                click.echo(f"  Using fallback: patch file will be passed to {session.method_label}")

            if session.fake_review:
                analysis = create_mock_review_output(review_id)
                click.echo("  [FAKE REVIEW] Using mock response")
            else:
                analysis = session.reviewer_fn(
                    review_id,
                    review.full_summary,
                    raw_diff,
                    repo_path,
                    repo_config,
                    used_target,
                )

    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Dump raw response
    if dump_response:
        dump_response.write_text(analysis)
        click.echo(f"Raw {session.method_label} response saved to: {dump_response}")

    # Parse and display
    parsed = parse_opencode_output(analysis)

    click.echo("\n" + "=" * 60)
    click.echo(f"{session.method_label} Analysis:")
    click.echo("=" * 60)
    click.echo(analysis)
    click.echo("=" * 60)

    # Build output data
    output_data = build_submission_data(
        review_id,
        analysis,
        parsed,
        session.model,
        rr_summary=review.summary,
        method_label=session.method_label,
    )

    # Save to file
    if auto_output:
        output_file = Path(f"review_{review_id}.json")
    elif output:
        output_file = output
    else:
        output_file = Path(f"review_{review_id}.json")

    output_file.write_text(json.dumps(output_data, indent=2))
    click.echo(f"\nReview saved to: {output_file}")
    click.echo(f"To submit: bb-review submit {output_file}")

    # Save to DB
    if session.config.review_db.enabled:
        save_to_review_db(
            config=session.config,
            review_id=review_id,
            diff_revision=diff_info.diff_revision,
            repository=repo_config.name,
            parsed=parsed,
            model=session.model or session.default_model or "default",
            analysis_method=session.analysis_method,
            rr_summary=review.summary,
            fake=session.fake_review,
            body_top=output_data.get("body_top"),
        )


def _run_chain_review(
    session: ReviewSession,
    review_id: int,
    review_chain: ReviewChain,
    pending: list,
    repo_config,
    timeout: int,
    dump_response: Path | None,
    auto_output: bool,
    fallback: bool,
    keep_branch: bool,
) -> None:
    """Run chain review for multiple pending patches."""
    branch_name = generate_branch_name(review_id)
    output_files: list[Path] = []

    try:
        with session.repo_manager.chain_context(
            repo_config.name,
            review_chain.base_commit,
            branch_name,
            keep_branch=keep_branch,
        ) as repo_path:
            click.echo(f"\nCreated branch: {branch_name}")

            # Apply context-only patches
            context_patches = [r for r in review_chain.reviews if not r.needs_review]
            for review in context_patches:
                rr_id = review.review_request_id
                click.echo(f"\nApplying context patch r/{rr_id}...")
                diff_info = session.rb_client.get_diff(rr_id, review.diff_revision)
                if not session.repo_manager.apply_and_commit(
                    repo_config.name,
                    diff_info.raw_diff,
                    f"r/{rr_id}: {review.summary[:50]}",
                ):
                    click.echo(f"  ERROR: Failed to apply context patch r/{rr_id}", err=True)
                    break
                click.echo("  Applied and committed")

            # Review pending patches
            for i, review in enumerate(pending):
                rr_id = review.review_request_id
                click.echo(f"\nReviewing r/{rr_id} ({i + 1}/{len(pending)})...")
                click.echo(f"  Summary: {review.summary[:60]}...")

                diff_info = session.rb_client.get_diff(rr_id, review.diff_revision)

                # Commit previous reviewed patch
                if i > 0:
                    prev_review = pending[i - 1]
                    if not session.repo_manager.commit_staged(
                        repo_config.name,
                        f"r/{prev_review.review_request_id}: {prev_review.summary[:50]}",
                    ):
                        click.echo(
                            f"  ERROR: Failed to commit patch for r/{prev_review.review_request_id}",
                            err=True,
                        )
                        break

                # Apply current patch
                patch_applied = session.repo_manager.apply_patch(repo_config.name, diff_info.raw_diff)
                if not patch_applied:
                    if fallback:
                        click.echo(
                            "  WARNING: Patch failed to apply, using fallback mode",
                            err=True,
                        )
                    else:
                        click.echo(f"  ERROR: Failed to apply patch for r/{rr_id}", err=True)
                        break

                # Run review
                if session.fake_review:
                    analysis = create_mock_review_output(rr_id)
                    click.echo("  [FAKE REVIEW] Using mock response")
                else:
                    analysis = session.reviewer_fn(
                        rr_id,
                        review.full_summary,
                        diff_info.raw_diff,
                        repo_path,
                        repo_config,
                        patch_applied,
                    )

                parsed = parse_opencode_output(analysis)

                output_data = build_submission_data(
                    review_id=rr_id,
                    analysis=analysis,
                    parsed=parsed,
                    model=session.model,
                    rr_summary=review.summary,
                    method_label=session.method_label,
                )

                if auto_output:
                    output_path = Path(f"review_{rr_id}.json")
                    output_path.write_text(json.dumps(output_data, indent=2))
                    output_files.append(output_path)
                    click.echo(f"  Saved: {output_path}")

                if session.config.review_db.enabled:
                    save_to_review_db(
                        config=session.config,
                        review_id=rr_id,
                        diff_revision=diff_info.diff_revision,
                        repository=review_chain.repository,
                        parsed=parsed,
                        model=session.model or session.default_model or "default",
                        analysis_method=session.analysis_method,
                        rr_summary=review.summary,
                        chain_id=branch_name if len(pending) > 1 else None,
                        chain_position=i + 1 if len(pending) > 1 else None,
                        fake=session.fake_review,
                        body_top=output_data.get("body_top"),
                    )

                if dump_response and i == len(pending) - 1:
                    dump_response.write_text(analysis)
                    click.echo(f"  Raw response saved to: {dump_response}")
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if keep_branch:
        click.echo(f"\nKept branch: {branch_name}")

    click.echo("\n" + "=" * 50)
    click.echo("Chain review complete.")
    click.echo(f"  Reviewed: {len(output_files)} patches")

    if output_files:
        click.echo("\nOutput files:")
        for f in output_files:
            click.echo(f"  - {f}")
        click.echo("\nTo submit reviews:")
        for f in output_files:
            click.echo(f"  bb-review submit {f}")


def _split_issues_by_rr(
    issues: list,
    file_to_rr: dict[str, int],
    fallback_rr_id: int,
) -> dict[int, list]:
    """Partition parsed issues by target RR based on which diff owns each file.

    Tries exact match on file_path, then suffix match (like _find_filediff_id).
    General issues (no file_path) and unmatched files go to fallback_rr_id.
    """
    result: dict[int, list] = {}

    for issue in issues:
        rr_id = fallback_rr_id

        if issue.file_path:
            path = issue.file_path
            if path in file_to_rr:
                rr_id = file_to_rr[path]
            else:
                # Suffix match: LLM may return a shorter or longer path
                for mapped_path, mapped_rr in file_to_rr.items():
                    if mapped_path.endswith(path) or path.endswith(mapped_path):
                        rr_id = mapped_rr
                        break
                else:
                    logger.warning(f"No RR mapping for file {path}, assigning to tip r/{fallback_rr_id}")

        result.setdefault(rr_id, []).append(issue)

    return result


def _run_series_review(
    session: ReviewSession,
    review_id: int,
    review_chain: ReviewChain,
    repo_config,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    keep_branch: bool,
) -> None:
    """Apply all patches as commits, then run a single review of the whole series."""
    branch_name = generate_branch_name(review_id)
    all_reviews = review_chain.reviews

    try:
        with session.repo_manager.chain_context(
            repo_config.name,
            review_chain.base_commit,
            branch_name,
            keep_branch=keep_branch,
        ) as repo_path:
            click.echo(f"\nCreated branch: {branch_name}")

            # Apply all patches as commits
            for review in all_reviews:
                rr_id = review.review_request_id
                click.echo(f"  Applying r/{rr_id}: {review.summary[:60]}...")
                diff_info = session.rb_client.get_diff(rr_id, review.diff_revision)
                if not session.repo_manager.apply_and_commit(
                    repo_config.name,
                    diff_info.raw_diff,
                    f"r/{rr_id}: {review.summary[:50]}",
                ):
                    click.echo(f"  ERROR: Failed to apply patch r/{rr_id}", err=True)
                    sys.exit(1)

            # Compute base_ref for git diff/log commands
            base_ref = review_chain.base_commit or f"origin/{repo_config.default_branch}"

            click.echo(
                f"\nAll {len(all_reviews)} patches applied. Running {session.method_label} series review..."
            )

            # Run the series review
            if session.fake_review:
                analysis = create_mock_review_output(review_id)
                click.echo("  [FAKE REVIEW] Using mock response")
            else:
                analysis = session.series_reviewer_fn(all_reviews, base_ref, repo_path, repo_config)

    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if keep_branch:
        click.echo(f"\nKept branch: {branch_name}")

    # Dump raw response
    if dump_response:
        dump_response.write_text(analysis)
        click.echo(f"Raw {session.method_label} response saved to: {dump_response}")

    # Parse and display
    parsed = parse_opencode_output(analysis)

    click.echo("\n" + "=" * 60)
    click.echo(f"{session.method_label} Series Analysis:")
    click.echo("=" * 60)
    click.echo(analysis)
    click.echo("=" * 60)

    # Build output for the target review (tip of chain)
    target_rr = review_chain.target_review
    target_rr_id = target_rr.review_request_id if target_rr else review_id

    output_data = build_submission_data(
        review_id=target_rr_id,
        analysis=analysis,
        parsed=parsed,
        model=session.model,
        rr_summary=target_rr.summary if target_rr else None,
        method_label=session.method_label,
    )

    # Save to file
    if output:
        output_file = output
    else:
        output_file = Path(f"review_{target_rr_id}.json")

    output_file.write_text(json.dumps(output_data, indent=2))
    click.echo(f"\nSeries review saved to: {output_file}")
    click.echo(f"To submit: bb-review submit {output_file}")

    # Save to DB
    if session.config.review_db.enabled:
        save_to_review_db(
            config=session.config,
            review_id=target_rr_id,
            diff_revision=target_rr.diff_revision if target_rr else 1,
            repository=review_chain.repository,
            parsed=parsed,
            model=session.model or session.default_model or "default",
            analysis_method=session.analysis_method,
            rr_summary=target_rr.summary if target_rr else None,
            chain_id=branch_name,
            fake=session.fake_review,
            body_top=output_data.get("body_top"),
        )
