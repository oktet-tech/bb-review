"""Analyze command for BB Review CLI."""

from datetime import datetime
import json
import logging
from pathlib import Path
import sys

import click

from ..config import Config
from ..git import RepoManager
from ..guidelines import load_guidelines, validate_guidelines
from ..models import ChainReviewResult, PendingReview, ReviewComment, ReviewFocus, ReviewResult, Severity
from ..reviewers import Analyzer, extract_changed_files, filter_diff_by_paths
from ..rr import (
    ChainError,
    DiffInfo,
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


def generate_chain_id(target_rr_id: int) -> str:
    """Generate a unique chain ID for tracking."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{target_rr_id}_{timestamp}"


def create_mock_review(review_id: int, diff_revision: int) -> ReviewResult:
    """Create a mock review result for --fake-review mode."""
    return ReviewResult(
        review_request_id=review_id,
        diff_revision=diff_revision,
        comments=[
            ReviewComment(
                file_path="example.c",
                line_number=42,
                message="[MOCK] This is a fake review comment for testing.",
                severity=Severity.LOW,
                issue_type=ReviewFocus.STYLE,
                suggestion="No actual suggestion - this is mock data.",
            )
        ],
        summary="[MOCK REVIEW] This is a fake review generated for testing purposes.",
        has_critical_issues=False,
    )


@main.command()
@click.argument("review_id", type=REVIEW_ID)
@click.option("--dry-run", is_flag=True, help="Do git ops but skip LLM calls, show what would be analyzed")
@click.option("--fake-review", is_flag=True, help="Do everything but use mock LLM responses (for testing)")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "markdown"]), default="text")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw LLM response to file")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output review to JSON file")
@click.option("-O", "--auto-output", is_flag=True, help="Auto-generate output file: review_{rr_id}.json")
@click.option("--chain/--no-chain", default=True, help="Auto-resolve dependency chain (default: --chain)")
@click.option(
    "--chain-file",
    type=click.Path(exists=True, path_type=Path),
    help="Manual chain file (one RR ID per line)",
)
@click.option("--base-commit", help="Base commit SHA for chain (used with --chain-file)")
@click.option("--keep-branch", is_flag=True, help="Don't delete the review branch after completion")
@click.option(
    "--fallback",
    is_flag=True,
    help="If patch doesn't apply cleanly, analyze with diff only (no file context)",
)
@click.option(
    "--review-from",
    type=REVIEW_ID,
    help="Start reviewing from this RR (earlier patches applied as context only)",
)
@click.pass_context
def analyze(
    ctx: click.Context,
    review_id: int,
    dry_run: bool,
    fake_review: bool,
    output_format: str,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    chain: bool,
    chain_file: Path | None,
    base_commit: str | None,
    keep_branch: bool,
    fallback: bool,
    review_from: int | None,
) -> None:
    """Analyze a review request using LLM.

    REVIEW_ID can be either a number (e.g., 42738) or a full Review Board URL
    (e.g., https://rb.example.com/r/42738/).

    By default, reviews the entire dependency chain. Use --no-chain to review
    only the specified review request.

    Use --review-from to start reviewing from a specific patch in the chain,
    applying earlier patches as context only (not reviewed).

    Results are output to stdout by default. Use -O to auto-generate output
    files (review_{rr_id}.json for each review).

    Examples:
        # Review entire chain
        bb-review analyze 42763

        # Review only 42763 (apply 42761, 42762 as context)
        bb-review analyze 42763 --review-from 42763

        # Review 42762 and 42763 (apply 42761 as context)
        bb-review analyze 42763 --review-from 42762
    """
    if output and auto_output:
        raise click.UsageError("Cannot use both -o and -O options")

    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    click.echo(f"Analyzing review request #{review_id}...")

    try:
        # Initialize components
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

        # Initialize analyzer (may not be used if --dry-run)
        analyzer = None
        if not dry_run:
            analyzer = Analyzer(
                api_key=config.llm.api_key,
                model=config.llm.model,
                max_tokens=config.llm.max_tokens,
                temperature=config.llm.temperature,
                provider=config.llm.provider,
                base_url=config.llm.base_url,
                site_url=config.llm.site_url,
                site_name=config.llm.site_name or "BB Review",
            )

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
            # Single review mode - create a chain with one item
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

            if keep_branch:
                click.echo(f"  [DRY RUN] Keep branch: {branch_name}")
            else:
                click.echo(f"  [DRY RUN] Delete branch: {branch_name}")

            click.echo("\nDry run complete. Use without --dry-run to perform actual review.")
            return

        # Perform actual review
        chain_result = ChainReviewResult(
            chain_id=generate_chain_id(review_id),
            repository=review_chain.repository,
        )
        branch_name = generate_branch_name(review_id)

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
                    chain_result.partial = True
                    chain_result.failed_at_rr_id = rr_id
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
                        rr = prev_review.review_request_id
                        click.echo(f"  ERROR: Failed to commit patch for r/{rr}", err=True)
                        chain_result.partial = True
                        chain_result.failed_at_rr_id = rr_id
                        break

                # Apply current patch (staged, not committed - we're reviewing it)
                patch_applied = repo_manager.apply_patch(repo_config.name, diff_info.raw_diff)
                if not patch_applied:
                    if fallback:
                        click.echo(
                            "  WARNING: Patch failed to apply, using fallback mode",
                            err=True,
                        )
                    else:
                        click.echo(f"  ERROR: Failed to apply patch for r/{rr_id}", err=True)
                        chain_result.partial = True
                        chain_result.failed_at_rr_id = rr_id
                        break

                # Generate review
                if fake_review:
                    result = create_mock_review(rr_id, diff_info.diff_revision)
                    click.echo("  [FAKE REVIEW] Using mock LLM response")
                else:
                    result = run_analysis(
                        rr_id,
                        diff_info,
                        repo_path,
                        repo_config.name,
                        repo_manager,
                        analyzer,
                        config,
                        patch_applied=patch_applied,
                    )

                chain_result.add_review(result)
                click.echo(f"  Found {result.issue_count} issues")

                # Save to file if requested
                if auto_output:
                    output_path = Path(f"review_{rr_id}.json")
                    save_review_to_file(result, output_path)
                    click.echo(f"  Saved: {output_path}")
                elif output and len(pending) == 1:
                    save_review_to_file(result, output)
                    click.echo(f"  Saved: {output}")

                # Save to reviews database if enabled
                if config.review_db.enabled:
                    _save_to_review_db(
                        config=config,
                        result=result,
                        repository=review_chain.repository,
                        diff_info=diff_info,
                        rr_summary=review.summary,
                        chain_id=chain_result.chain_id if len(pending) > 1 else None,
                        chain_position=i + 1 if len(pending) > 1 else None,
                        model=config.llm.model,
                        fake=fake_review,
                    )

                # Note: staged changes are kept and will be committed at the start
                # of the next iteration (or left staged if this is the last patch)

        # Final output
        if keep_branch:
            chain_result.branch_name = branch_name
            click.echo(f"\nKept branch: {branch_name}")

        # Save chain info to database if enabled and multiple reviews
        if config.review_db.enabled and len(chain_result.reviews) > 1:
            _save_chain_to_review_db(config, chain_result)

        # Dump raw response if requested (for last review)
        if dump_response and analyzer:
            raw = analyzer.get_last_raw_response()
            if raw is not None:
                dump_response.write_text(raw if raw else "(empty response)")
                click.echo(f"Raw LLM response saved to: {dump_response}")

        # Summary
        click.echo("\n" + "=" * 50)
        click.echo("Chain review complete.")
        click.echo(f"  Reviewed: {chain_result.reviewed_count} of {len(pending)} patches")
        click.echo(f"  Total issues: {chain_result.total_issues}")

        if chain_result.partial:
            click.echo(f"  WARNING: Partial review - failed at r/{chain_result.failed_at_rr_id}")

        if auto_output:
            click.echo("\nOutput files:")
            for r in chain_result.reviews:
                click.echo(f"  - review_{r.review_request_id}.json")
            click.echo("\nTo submit reviews:")
            for r in chain_result.reviews:
                click.echo(f"  bb-review submit review_{r.review_request_id}.json")

        # Output format for single review
        if len(chain_result.reviews) == 1 and not auto_output:
            result = chain_result.reviews[0]
            if output_format == "json":
                click.echo(json.dumps(ReviewFormatter.format_as_json(result), indent=2))
            elif output_format == "markdown":
                click.echo(ReviewFormatter.format_as_markdown(result))
            else:
                # Text format - simple output
                click.echo(f"\nReview r/{result.review_request_id} (diff {result.diff_revision}):")
                click.echo(f"Summary: {result.summary}")
                click.echo(f"Issues: {result.issue_count}")
                for comment in result.comments:
                    sev = comment.severity.value.upper()
                    loc = f"{comment.file_path}:{comment.line_number}"
                    click.echo(f"\n  [{sev}] {loc}")
                    click.echo(f"  {comment.message}")

    except ChainError as e:
        click.echo(f"Chain resolution error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Failed to analyze review")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def run_analysis(
    review_id: int,
    diff_info,
    repo_path: Path,
    repo_name: str,
    repo_manager: RepoManager,
    analyzer: Analyzer,
    config: Config,
    patch_applied: bool = True,
) -> ReviewResult:
    """Run LLM analysis on a single review.

    Args:
        patch_applied: If True, the patch is staged in git and we can get file context.
            If False (fallback mode), only the diff is available.
    """
    # Load guidelines
    guidelines = load_guidelines(repo_path)

    # Validate guidelines
    warnings = validate_guidelines(guidelines)
    for warning in warnings:
        click.echo(f"    Warning: {warning}", err=True)

    # Use the diff
    diff = diff_info.raw_diff

    # Filter ignored paths
    if guidelines.ignore_paths:
        diff = filter_diff_by_paths(diff, guidelines.ignore_paths)

    # Get file context for changed files (only if patch is applied)
    file_contexts = {}
    changed_files = extract_changed_files(diff)
    if patch_applied:
        for file_info in changed_files[:10]:  # Limit context gathering
            file_path = file_info["path"]
            if file_info["lines"]:
                context = repo_manager.get_file_context(
                    repo_name,
                    file_path,
                    min(file_info["lines"]),
                    max(file_info["lines"]),
                    context_lines=30,
                )
                if context:
                    file_contexts[file_path] = context

    # Run analysis
    click.echo(f"    Analyzing diff ({len(diff)} chars)...")
    return analyzer.analyze(
        diff=diff,
        guidelines=guidelines,
        file_contexts=file_contexts,
        review_request_id=review_id,
        diff_revision=diff_info.diff_revision,
    )


def save_review_to_file(result: ReviewResult, path: Path) -> None:
    """Save a review result to a JSON file."""
    data = ReviewFormatter.format_as_json(result)
    path.write_text(json.dumps(data, indent=2))


def _save_to_review_db(
    config: Config,
    result: ReviewResult,
    repository: str,
    diff_info: DiffInfo,
    rr_summary: str | None = None,
    chain_id: str | None = None,
    chain_position: int | None = None,
    model: str = "",
    fake: bool = False,
) -> None:
    """Save a review result to the reviews database."""
    from ..db import ReviewDatabase
    from ..rr.rb_client import ReviewRequestInfo

    # Create a minimal rr_info to pass the summary
    rr_info = None
    if rr_summary:
        rr_info = ReviewRequestInfo(
            id=result.review_request_id,
            summary=rr_summary,
            status="pending",
            repository_name=repository,
            depends_on=[],
            base_commit_id=diff_info.base_commit_id if diff_info else None,
            diff_revision=result.diff_revision,
        )

    # Build the RB URL for this review request
    rb_url = f"{config.reviewboard.url.rstrip('/')}/r/{result.review_request_id}/"

    try:
        review_db = ReviewDatabase(config.review_db.resolved_path)

        # Ensure chain exists if we have a chain_id
        if chain_id:
            review_db.ensure_chain_exists(chain_id, repository)

        analysis_id = review_db.save_analysis(
            result=result,
            repository=repository,
            analysis_method="llm",
            model=model,
            diff_info=diff_info,
            rr_info=rr_info,
            chain_id=chain_id,
            chain_position=chain_position,
            fake=fake,
            rb_url=rb_url,
        )
        logger.debug(f"Saved analysis {analysis_id} to reviews database")
    except Exception as e:
        logger.warning(f"Failed to save to reviews database: {e}")


def _save_chain_to_review_db(config: Config, chain_result: ChainReviewResult) -> None:
    """Save chain info to the reviews database."""
    from ..db import ReviewDatabase

    try:
        review_db = ReviewDatabase(config.review_db.resolved_path)
        # Insert chain record (analyses were already saved individually)
        with review_db._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO chains (
                    chain_id, created_at, repository, partial,
                    failed_at_rr_id, branch_name
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chain_result.chain_id,
                    datetime.now().isoformat(),
                    chain_result.repository,
                    1 if chain_result.partial else 0,
                    chain_result.failed_at_rr_id,
                    chain_result.branch_name,
                ),
            )
        logger.debug(f"Saved chain {chain_result.chain_id} to reviews database")
    except Exception as e:
        logger.warning(f"Failed to save chain to reviews database: {e}")


# Keep old process_review for backward compatibility with other modules
def process_review(
    review_id: int,
    rb_client: ReviewBoardClient,
    repo_manager: RepoManager,
    analyzer: Analyzer,
    config: Config,
    pending: PendingReview | None = None,
):
    """Process a single review request (legacy interface)."""

    # Get review info if not provided
    if pending is None:
        rr = rb_client.get_review_request(review_id)
        repo_info = rb_client.get_repository_info(review_id)
        diff_info = rb_client.get_diff(review_id)

        pending = PendingReview(
            review_request_id=review_id,
            repository=repo_info["name"],
            submitter=rr.get("submitter", {}).get("username", "unknown"),
            summary=rr.get("summary", ""),
            diff_revision=diff_info.diff_revision,
            base_commit=diff_info.base_commit_id,
            branch=rr.get("branch"),
        )
    else:
        diff_info = rb_client.get_diff(review_id)

    # Get repository config
    repo_config = repo_manager.get_repo_by_rb_name(pending.repository)
    if repo_config is None:
        raise click.ClickException(
            f"Repository not configured: {pending.repository}. Add it to config.yaml under 'repositories'."
        )

    # Checkout the commit (with patch applied if target commit unavailable)
    raw_diff = diff_info.raw_diff

    with repo_manager.checkout_context(
        repo_config.name,
        base_commit=pending.base_commit,
        branch=pending.branch,
        target_commit=diff_info.target_commit_id,
        patch=raw_diff,
    ) as (repo_path, used_target):
        return run_analysis(
            review_id,
            diff_info,
            repo_path,
            repo_config.name,
            repo_manager,
            analyzer,
            config,
        )
