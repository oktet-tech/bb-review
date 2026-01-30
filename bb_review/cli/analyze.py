"""Analyze command for BB Review CLI."""

import json
import logging
from pathlib import Path
import sys

import click

from ..config import Config
from ..git import RepoManager
from ..guidelines import load_guidelines, validate_guidelines
from ..models import PendingReview
from ..reviewers import Analyzer, extract_changed_files, filter_diff_by_paths
from ..rr import Commenter, ReviewBoardClient, ReviewFormatter
from . import get_config, main
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


@main.command()
@click.argument("review_id", type=REVIEW_ID)
@click.option("--dry-run", is_flag=True, help="Don't post, just show what would be posted")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "markdown"]), default="text")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw LLM response to file")
@click.pass_context
def analyze(
    ctx: click.Context,
    review_id: int,
    dry_run: bool,
    output_format: str,
    dump_response: Path | None,
) -> None:
    """Analyze a specific review request.

    REVIEW_ID can be either a number (e.g., 42738) or a full Review Board URL
    (e.g., https://rb.example.com/r/42738/).
    """
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

        commenter = Commenter(
            rb_client=rb_client,
            analyzer=analyzer,
            auto_ship_it=config.defaults.auto_ship_it,
        )

        # Process the review
        result = process_review(
            review_id=review_id,
            rb_client=rb_client,
            repo_manager=repo_manager,
            analyzer=analyzer,
            config=config,
        )

        # Dump raw response if requested
        if dump_response:
            raw = analyzer.get_last_raw_response()
            if raw is not None:
                dump_response.write_text(raw if raw else "(empty response)")
                click.echo(f"Raw LLM response saved to: {dump_response}")
                if not raw:
                    click.echo("Warning: LLM returned empty response", err=True)
            else:
                click.echo("No raw response available (analysis may have failed)", err=True)

        # Output result
        if output_format == "json":
            click.echo(json.dumps(ReviewFormatter.format_as_json(result), indent=2))
        elif output_format == "markdown":
            click.echo(ReviewFormatter.format_as_markdown(result))
        else:
            click.echo(commenter.format_cli_output(result))

        # Post if not dry run
        if not dry_run:
            review_posted = commenter.post_review(result, dry_run=False)
            click.echo(f"\nPosted review (ID: {review_posted})")
        else:
            commenter.post_review(result, dry_run=True)

    except Exception as e:
        logger.exception("Failed to analyze review")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def process_review(
    review_id: int,
    rb_client: ReviewBoardClient,
    repo_manager: RepoManager,
    analyzer: Analyzer,
    config: Config,
    pending: PendingReview | None = None,
):
    """Process a single review request."""
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

    # Get repository config
    repo_config = repo_manager.get_repo_by_rb_name(pending.repository)
    if repo_config is None:
        raise click.ClickException(
            f"Repository not configured: {pending.repository}. Add it to config.yaml under 'repositories'."
        )

    # Checkout the commit (with patch applied if target commit unavailable)
    click.echo(f"  Repository: {repo_config.name}")
    if diff_info.target_commit_id:
        click.echo(f"  Target commit: {diff_info.target_commit_id[:12]} (reviewing actual commit)")
    click.echo(f"  Base commit: {pending.base_commit or 'default branch'}")

    # Get raw diff for potential patch application
    raw_diff = diff_info.raw_diff

    with repo_manager.checkout_context(
        repo_config.name,
        base_commit=pending.base_commit,
        branch=pending.branch,
        target_commit=diff_info.target_commit_id,
        patch=raw_diff,
    ) as (repo_path, used_target):
        if used_target:
            click.echo("  Checked out to reviewed state")

        # Load guidelines
        guidelines = load_guidelines(repo_path)

        # Validate guidelines
        warnings = validate_guidelines(guidelines)
        for warning in warnings:
            click.echo(f"  Warning: {warning}", err=True)

        # Use the diff we already have
        diff = raw_diff

        # Filter ignored paths
        if guidelines.ignore_paths:
            diff = filter_diff_by_paths(diff, guidelines.ignore_paths)

        # Get file context for changed files
        file_contexts = {}
        changed_files = extract_changed_files(diff)
        for file_info in changed_files[:10]:  # Limit context gathering
            file_path = file_info["path"]
            if file_info["lines"]:
                context = repo_manager.get_file_context(
                    repo_config.name,
                    file_path,
                    min(file_info["lines"]),
                    max(file_info["lines"]),
                    context_lines=30,
                )
                if context:
                    file_contexts[file_path] = context

        # Run analysis
        click.echo(f"  Analyzing diff ({len(diff)} chars)...")
        result = analyzer.analyze(
            diff=diff,
            guidelines=guidelines,
            file_contexts=file_contexts,
            review_request_id=review_id,
            diff_revision=diff_info.diff_revision,
        )

        click.echo(f"  Found {result.issue_count} issues")
        return result
