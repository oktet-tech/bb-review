"""OpenCode command for BB Review CLI."""

from datetime import datetime
import json
import logging
from pathlib import Path
import sys

import click

from ..git import PatchApplyError, RepoManager
from ..guidelines import load_guidelines, validate_guidelines
from ..models import ReviewComment, ReviewFocus, ReviewResult, Severity
from ..reviewers import (
    OpenCodeError,
    OpenCodeTimeoutError,
    build_review_prompt,
    check_opencode_available,
    extract_changed_files,
    filter_diff_by_paths,
    parse_opencode_output,
    run_opencode_agent,
    run_opencode_review,
)
from ..rr import (
    ChainError,
    ReviewBoardClient,
    ReviewChain,
    ReviewFormatter,
    load_chain_from_file,
    resolve_chain,
)
from . import get_config, main
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


def generate_branch_name(target_rr_id: int) -> str:
    """Generate a unique branch name for chain review."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"bb_review_{target_rr_id}_{timestamp}"


@main.command("opencode")
@click.argument("review_id", type=REVIEW_ID)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Do git ops but skip OpenCode calls, show what would be analyzed",
)
@click.option(
    "--fake-review",
    is_flag=True,
    help="Do everything but use mock OpenCode responses (for testing)",
)
@click.option("--model", "-m", help="Override opencode model")
@click.option("--timeout", default=300, type=int, help="Timeout in seconds for opencode")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw response to file")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output JSON file")
@click.option("-O", "--auto-output", is_flag=True, help="Auto-generate output: review_{id}.json")
@click.option(
    "--fallback",
    is_flag=True,
    help="If patch doesn't apply cleanly, pass patch file to OpenCode",
)
@click.option(
    "--chain/--no-chain",
    default=True,
    help="Auto-resolve dependency chain (default: --chain)",
)
@click.option(
    "--chain-file",
    type=click.Path(exists=True, path_type=Path),
    help="Manual chain file (one RR ID per line)",
)
@click.option("--base-commit", help="Base commit SHA for chain (used with --chain-file)")
@click.option(
    "--keep-branch",
    is_flag=True,
    help="Don't delete the review branch after completion",
)
@click.option(
    "--review-from",
    type=REVIEW_ID,
    help="Start reviewing from this RR (earlier patches applied as context only)",
)
@click.pass_context
def opencode_cmd(
    ctx: click.Context,
    review_id: int,
    dry_run: bool,
    fake_review: bool,
    model: str | None,
    timeout: int,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    fallback: bool,
    chain: bool,
    chain_file: Path | None,
    base_commit: str | None,
    keep_branch: bool,
    review_from: int | None,
) -> None:
    """Analyze a review using OpenCode agent.

    REVIEW_ID can be either a number (e.g., 42738) or a full Review Board URL
    (e.g., https://rb.example.com/r/42738/).

    By default, reviews the entire dependency chain. Use --no-chain to review
    only the specified review request.

    Results are output to stdout by default. Use -O to auto-generate output
    files (review_{rr_id}.json for each review).
    """
    # Validate output options
    if output and auto_output:
        raise click.UsageError("Cannot use both -o/--output and -O/--auto-output")

    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    # Check if opencode is available (unless dry-run or fake)
    binary_path = config.opencode.binary_path
    if not dry_run and not fake_review:
        available, msg = check_opencode_available(binary_path)
        if not available:
            click.echo(f"Error: {msg}", err=True)
            sys.exit(1)
        logger.debug(msg)

    # Use config values if not overridden
    if model is None:
        model = config.opencode.model
    if timeout == 300:  # default value
        timeout = config.opencode.timeout

    click.echo(f"Analyzing review request #{review_id} with OpenCode...")

    try:
        # Initialize RB client
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            bot_username=config.reviewboard.bot_username,
            api_token=config.reviewboard.api_token,
            username=config.reviewboard.username,
            password=config.reviewboard.get_password(),
            use_kerberos=config.reviewboard.use_kerberos,
        )
        rb_client.connect()

        repo_manager = RepoManager(config.get_all_repos())

        # Resolve the chain
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
            # Single review mode
            rr_info = rb_client.get_review_request_info(review_id)
            review_chain = ReviewChain(
                repository=rr_info.repository_name,
                base_commit=rr_info.base_commit_id,
            )
            from ..rr.chain import ChainedReview

            review_chain.reviews.append(
                ChainedReview(
                    review_request_id=review_id,
                    summary=rr_info.summary,
                    status=rr_info.status,
                    diff_revision=rr_info.diff_revision,
                    base_commit_id=rr_info.base_commit_id,
                    needs_review=True,
                )
            )

        # Apply --review-from filter if specified
        if review_from is not None:
            # Validate that review_from is in the chain
            chain_ids = [r.review_request_id for r in review_chain.reviews]
            if review_from not in chain_ids:
                raise click.ClickException(
                    f"Review r/{review_from} is not in the chain: {chain_ids}. "
                    f"Use one of the reviews in the chain."
                )

            # Mark reviews before review_from as not needing review
            found_start = False
            for review in review_chain.reviews:
                if review.review_request_id == review_from:
                    found_start = True
                if not found_start:
                    review.needs_review = False
                    click.echo(f"  Skipping review of r/{review.review_request_id} (context only)")

        # Display chain info
        pending = review_chain.pending_reviews
        if len(pending) == 0:
            click.echo("No pending reviews to analyze in chain.")
            return

        chain_str = " -> ".join(f"r/{r.review_request_id}" for r in review_chain.reviews)
        click.echo(f"  Chain: {chain_str}")
        click.echo(f"  To review: {len(pending)} patch(es)")
        click.echo(f"  Base commit: {review_chain.base_commit or 'default branch'}")

        # Get repository config
        repo_config = repo_manager.get_repo_by_rb_name(review_chain.repository)
        if repo_config is None:
            raise click.ClickException(
                f"Repository not configured: {review_chain.repository}. "
                "Add it to config.yaml under 'repositories'."
            )

        # Dry run mode - just show what would be analyzed
        if dry_run:
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
                click.echo(f"    Would run OpenCode with model: {model or 'default'}")

            if keep_branch:
                click.echo(f"  [DRY RUN] Keep branch: {branch_name}")
            else:
                click.echo(f"  [DRY RUN] Delete branch: {branch_name}")

            click.echo("\nDry run complete. Use without --dry-run to perform actual review.")
            return

        # Single review mode - use existing checkout_context
        if len(pending) == 1:
            run_single_opencode_review(
                pending[0],
                rb_client,
                repo_manager,
                repo_config,
                config,
                model,
                timeout,
                binary_path,
                dump_response,
                output,
                auto_output,
                fallback,
                fake_review,
            )
            return

        # Chain review mode
        branch_name = generate_branch_name(review_id)
        output_files = []

        with repo_manager.chain_context(
            repo_config.name,
            review_chain.base_commit,
            branch_name,
            keep_branch=keep_branch,
        ) as repo_path:
            click.echo(f"\nCreated branch: {branch_name}")

            # First, apply all context-only patches (needs_review=False)
            context_patches = [r for r in review_chain.reviews if not r.needs_review]
            for review in context_patches:
                rr_id = review.review_request_id
                click.echo(f"\nApplying context patch r/{rr_id}...")
                diff_info = rb_client.get_diff(rr_id, review.diff_revision)
                if not repo_manager.apply_and_commit(
                    repo_config.name,
                    diff_info.raw_diff,
                    f"r/{rr_id}: {review.summary[:50]}",
                ):
                    click.echo(f"  ERROR: Failed to apply context patch r/{rr_id}", err=True)
                    break
                click.echo("  Applied and committed")

            # Now review the pending patches
            for i, review in enumerate(pending):
                rr_id = review.review_request_id
                click.echo(f"\nReviewing r/{rr_id} ({i + 1}/{len(pending)})...")
                click.echo(f"  Summary: {review.summary[:60]}...")

                # Fetch diff
                diff_info = rb_client.get_diff(rr_id, review.diff_revision)

                # Commit previous reviewed patch first (if not first)
                # The previous patch is already staged from the last iteration
                if i > 0:
                    prev_review = pending[i - 1]
                    if not repo_manager.commit_staged(
                        repo_config.name,
                        f"r/{prev_review.review_request_id}: {prev_review.summary[:50]}",
                    ):
                        click.echo(
                            f"  ERROR: Failed to commit patch for r/{prev_review.review_request_id}",
                            err=True,
                        )
                        break

                # Apply current patch (staged for review)
                patch_applied = repo_manager.apply_patch(repo_config.name, diff_info.raw_diff)
                if not patch_applied:
                    if fallback:
                        click.echo(
                            "  WARNING: Patch failed to apply, using fallback mode",
                            err=True,
                        )
                    else:
                        click.echo(f"  ERROR: Failed to apply patch for r/{rr_id}", err=True)
                        break

                # Run OpenCode review
                if fake_review:
                    analysis = create_mock_opencode_output(rr_id)
                    click.echo("  [FAKE REVIEW] Using mock OpenCode response")
                else:
                    analysis = run_opencode_for_review(
                        rr_id,
                        review.summary,
                        diff_info.raw_diff,
                        repo_path,
                        repo_config,
                        model,
                        timeout,
                        binary_path,
                        at_reviewed_state=patch_applied,
                    )

                # Parse and save
                parsed = parse_opencode_output(analysis)

                # Build output data
                output_data = build_submission_data(
                    review_id=rr_id,
                    analysis=analysis,
                    parsed=parsed,
                    model=model,
                )

                # Save to file
                if auto_output:
                    output_path = Path(f"review_{rr_id}.json")
                    output_path.write_text(json.dumps(output_data, indent=2))
                    output_files.append(output_path)
                    click.echo(f"  Saved: {output_path}")

                # Save to reviews database if enabled
                if config.review_db.enabled:
                    _save_opencode_to_review_db(
                        config=config,
                        review_id=rr_id,
                        diff_revision=diff_info.diff_revision,
                        repository=review_chain.repository,
                        parsed=parsed,
                        model=model or config.opencode.model or "default",
                        chain_id=branch_name if len(pending) > 1 else None,
                        chain_position=i + 1 if len(pending) > 1 else None,
                    )

                # Dump raw response if requested (last review)
                if dump_response and i == len(pending) - 1:
                    dump_response.write_text(analysis)
                    click.echo(f"  Raw response saved to: {dump_response}")

                # Note: staged changes are kept and will be committed at the start
                # of the next iteration (or left staged if this is the last patch)

        # Summary
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

    except ChainError as e:
        click.echo(f"Chain resolution error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Failed to analyze review with OpenCode")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def create_mock_opencode_output(review_id: int) -> str:
    """Create mock OpenCode output for --fake-review mode."""
    return f"""### Issue: [MOCK] Style Issue
**File:** example.c
**Line:** 42
**Severity:** low

[MOCK] This is a fake review comment for testing purposes.

**Suggestion:** No actual suggestion - this is mock data.

---

**Summary:** [MOCK REVIEW] This is a fake review generated for r/{review_id} for testing purposes.
"""


def run_opencode_for_review(
    review_id: int,
    summary: str,
    raw_diff: str,
    repo_path: Path,
    repo_config,
    model: str | None,
    timeout: int,
    binary_path: str | None,
    at_reviewed_state: bool = True,
) -> str:
    """Run OpenCode analysis for a single review.

    Args:
        at_reviewed_state: If True, changes are staged in git. If False,
            only the patch file is available (fallback mode).
    """
    # Load guidelines
    guidelines = load_guidelines(repo_path)

    # Validate guidelines
    warnings = validate_guidelines(guidelines)
    for warning in warnings:
        click.echo(f"    Warning: {warning}", err=True)

    # Filter ignored paths
    if guidelines.ignore_paths:
        raw_diff = filter_diff_by_paths(raw_diff, guidelines.ignore_paths)

    # Build guidelines context
    guidelines_context = ""
    if guidelines.context:
        guidelines_context = guidelines.context
    if guidelines.custom_rules:
        if guidelines_context:
            guidelines_context += "\n\nCustom rules:\n"
        guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)

    # Extract changed files
    changed_file_infos = extract_changed_files(raw_diff)
    changed_files = [f["path"] for f in changed_file_infos]

    # Build prompt
    focus_areas = [f.value for f in guidelines.focus]
    prompt = build_review_prompt(
        repo_name=repo_config.name,
        review_id=review_id,
        summary=summary,
        guidelines_context=guidelines_context,
        focus_areas=focus_areas,
        at_reviewed_state=at_reviewed_state,
        changed_files=changed_files,
    )

    click.echo(f"    Running OpenCode analysis ({len(raw_diff)} chars diff)...")

    try:
        return run_opencode_review(
            repo_path=repo_path,
            patch_content=raw_diff,
            prompt=prompt,
            review_id=review_id,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            at_reviewed_state=at_reviewed_state,
        )
    except OpenCodeTimeoutError as e:
        raise click.ClickException(f"OpenCode timed out after {timeout}s") from e
    except OpenCodeError as e:
        raise click.ClickException(str(e)) from e


def build_submission_data(
    review_id: int,
    analysis: str,
    parsed,
    model: str | None,
) -> dict:
    """Build submission data from parsed OpenCode output."""
    # Build comments
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

    # Build body_top
    body_parts = ["**AI Review (OpenCode)**\n"]

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
            "opencode": True,
        },
    )


def run_single_opencode_review(
    review,
    rb_client,
    repo_manager,
    repo_config,
    config,
    model: str | None,
    timeout: int,
    binary_path: str | None,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    fallback: bool,
    fake_review: bool,
) -> None:
    """Run OpenCode review for a single review request (legacy single mode)."""
    review_id = review.review_request_id
    diff_info = rb_client.get_diff(review_id, review.diff_revision)
    raw_diff = diff_info.raw_diff

    click.echo(f"  Repository: {repo_config.name}")
    if diff_info.target_commit_id:
        click.echo(f"  Target commit: {diff_info.target_commit_id[:12]}")
    click.echo(f"  Base commit: {review.base_commit_id or 'default branch'}")

    try:
        with repo_manager.checkout_context(
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
                click.echo("  Using fallback: patch file will be passed to OpenCode")

            if fake_review:
                analysis = create_mock_opencode_output(review_id)
                click.echo("  [FAKE REVIEW] Using mock OpenCode response")
            else:
                analysis = run_opencode_for_review(
                    review_id,
                    review.summary,
                    raw_diff,
                    repo_path,
                    repo_config,
                    model,
                    timeout,
                    binary_path,
                )

            # Run API review for te-test-suite repos
            api_analysis = None
            if repo_config.repo_type == "te-test-suite" and not fake_review:
                click.echo("  Running API review via api-reviewer agent...")
                api_analysis = run_api_review(
                    review_id,
                    review.summary,
                    raw_diff,
                    repo_path,
                    used_target,
                    model,
                    timeout,
                    binary_path,
                )

    except PatchApplyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Dump raw response if requested
    if dump_response:
        dump_content = analysis
        if api_analysis:
            dump_content += "\n\n" + "=" * 60 + "\nAPI Review:\n" + "=" * 60 + "\n" + api_analysis
        dump_response.write_text(dump_content)
        click.echo(f"Raw OpenCode response saved to: {dump_response}")

    # Parse and display
    parsed = parse_opencode_output(analysis)
    api_parsed = parse_opencode_output(api_analysis) if api_analysis else None

    click.echo("\n" + "=" * 60)
    click.echo("OpenCode Analysis:")
    click.echo("=" * 60)
    click.echo(analysis)
    click.echo("=" * 60)

    if api_analysis:
        click.echo("\n" + "=" * 60)
        click.echo("API Review:")
        click.echo("=" * 60)
        click.echo(api_analysis)
        click.echo("=" * 60)

    # Merge issues
    all_issues = list(parsed.issues)
    if api_parsed:
        for issue in api_parsed.issues:
            issue.title = f"[API] {issue.title}"
        all_issues.extend(api_parsed.issues)

    # Build output data
    output_data = build_submission_data(review_id, analysis, parsed, model)

    # Add API info if available
    if api_parsed:
        if api_parsed.unparsed_text:
            output_data["body_top"] += "\n\n## API Review Notes\n" + api_parsed.unparsed_text
        output_data["parsed_issues"].extend(
            [
                {
                    "title": f"[API] {issue.title}",
                    "file_path": issue.file_path,
                    "line_number": issue.line_number,
                    "severity": issue.severity,
                    "issue_type": issue.issue_type,
                    "comment": issue.comment,
                    "suggestion": issue.suggestion,
                }
                for issue in api_parsed.issues
            ]
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

    # Save to reviews database if enabled
    if config.review_db.enabled:
        # Merge API issues if present
        merged_parsed = parsed
        if api_parsed:
            # Create a simple merged parsed result
            from types import SimpleNamespace

            merged_parsed = SimpleNamespace(
                issues=all_issues,
                summary=parsed.summary or (api_parsed.summary if api_parsed else ""),
                unparsed_text=parsed.unparsed_text,
            )
        _save_opencode_to_review_db(
            config=config,
            review_id=review_id,
            diff_revision=diff_info.diff_revision,
            repository=repo_config.name,
            parsed=merged_parsed,
            model=model or config.opencode.model or "default",
        )


def run_api_review(
    review_id: int,
    summary: str,
    raw_diff: str,
    repo_path: Path,
    used_target: bool,
    model: str | None,
    timeout: int,
    binary_path: str | None,
) -> str | None:
    """Run API review for te-test-suite repos."""
    context_path = repo_path / ".bb_review_context.tmp"
    context_path.write_text(f"Review #{review_id}\n\nSummary:\n{summary}")

    try:
        if used_target:
            api_prompt = (
                "Review the staged changes (use `git diff --cached` to see them) "
                "with context @.bb_review_context.tmp"
            )
        else:
            patch_path = repo_path / ".bb_review_patch.tmp"
            patch_path.write_text(raw_diff)
            api_prompt = "Review the patch @.bb_review_patch.tmp with context @.bb_review_context.tmp"

        return run_opencode_agent(
            repo_path=repo_path,
            agent="api-reviewer",
            prompt=api_prompt,
            review_id=review_id,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
        )
    except OpenCodeTimeoutError:
        click.echo(f"  Warning: API review timed out after {timeout}s", err=True)
        return None
    except OpenCodeError as e:
        click.echo(f"  Warning: API review failed: {e}", err=True)
        return None
    finally:
        for tmp_file in [context_path, repo_path / ".bb_review_patch.tmp"]:
            try:
                tmp_file.unlink()
            except Exception:
                pass


def _save_opencode_to_review_db(
    config,
    review_id: int,
    diff_revision: int,
    repository: str,
    parsed,
    model: str,
    chain_id: str | None = None,
    chain_position: int | None = None,
) -> None:
    """Save an OpenCode review result to the reviews database."""
    from ..db import ReviewDatabase

    # Convert parsed issues to ReviewComment objects
    comments = []
    has_critical = False

    for issue in parsed.issues:
        # Map severity string to enum
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

        # Map issue_type to ReviewFocus
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

    # Create ReviewResult
    result = ReviewResult(
        review_request_id=review_id,
        diff_revision=diff_revision,
        comments=comments,
        summary=parsed.summary or "OpenCode analysis complete",
        has_critical_issues=has_critical,
    )

    try:
        review_db = ReviewDatabase(config.review_db.resolved_path)
        analysis_id = review_db.save_analysis(
            result=result,
            repository=repository,
            analysis_method="opencode",
            model=model,
            chain_id=chain_id,
            chain_position=chain_position,
        )
        logger.debug(f"Saved opencode analysis {analysis_id} to reviews database")
    except Exception as e:
        logger.warning(f"Failed to save to reviews database: {e}")
