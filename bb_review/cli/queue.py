"""Queue CLI commands for review triage workflow."""

import logging
import sys

import click

from ..config import Config
from ..db.queue_db import QueueDatabase
from ..db.queue_models import QueueStatus
from . import get_config, main


logger = logging.getLogger(__name__)


def _get_queue_db(config: Config) -> QueueDatabase:
    """Get QueueDatabase instance, sharing the reviews.db path."""
    if not config.review_db.enabled:
        click.echo("Error: Reviews database is not enabled in config.", err=True)
        sys.exit(1)
    return QueueDatabase(config.review_db.resolved_path)


@main.group()
def queue() -> None:
    """Review queue / triage workflow commands."""
    pass


@queue.command()
@click.option("--days", default=10, help="How far back to look (default: 10).")
@click.option("--limit", default=200, help="Max review requests to fetch.")
@click.option("--bot-only", is_flag=True, help="Only fetch RRs assigned to bot user.")
@click.option("--repo", "repository", help="Filter by repository name.")
@click.option("--submitter", help="Filter by submitter username.")
@click.pass_context
def sync(
    ctx: click.Context,
    days: int,
    limit: int,
    bot_only: bool,
    repository: str | None,
    submitter: str | None,
) -> None:
    """Sync review requests from Review Board into the queue."""
    config = get_config(ctx)
    queue_db = _get_queue_db(config)

    from ..rr.rb_client import ReviewBoardClient

    rb_client = ReviewBoardClient(
        url=config.reviewboard.url,
        bot_username=config.reviewboard.bot_username,
        api_token=config.reviewboard.api_token,
        username=config.reviewboard.username,
        password=config.reviewboard.get_password(),
        use_kerberos=config.reviewboard.use_kerberos,
    )
    rb_client.connect()

    from ..queue_sync import sync_queue

    click.echo(f"Syncing reviews (last {days} days, limit {limit})...")
    counts = sync_queue(
        rb_client=rb_client,
        queue_db=queue_db,
        days=days,
        limit=limit,
        repository=repository,
        submitter=submitter,
        bot_only=bot_only,
    )

    click.echo(
        f"Sync complete: {counts['total']} fetched, "
        f"{counts['inserted']} new, "
        f"{counts['updated']} reset, "
        f"{counts['skipped']} unchanged, "
        f"{counts['analyzed']} already analyzed"
    )


@queue.command("list")
@click.option(
    "--status",
    type=click.Choice([s.value for s in QueueStatus]),
    help="Filter by status.",
)
@click.option("--repo", "repository", help="Filter by repository name.")
@click.option("--limit", default=50, help="Max items to show.")
@click.pass_context
def list_items(
    ctx: click.Context,
    status: str | None,
    repository: str | None,
    limit: int,
) -> None:
    """List queue items."""
    config = get_config(ctx)
    queue_db = _get_queue_db(config)

    status_enum = QueueStatus(status) if status else None
    items = queue_db.list_items(status=status_enum, repository=repository, limit=limit)

    if not items:
        click.echo("No queue items found.")
        return

    click.echo(f"Found {len(items)} item(s):\n")

    # Table header
    click.echo(f"{'RR':>7}  {'Diff':>4}  {'Status':<12}  {'Repo':<20}  {'Submitter':<15}  Summary")
    click.echo("-" * 100)

    for item in items:
        summary = (item.summary or "")[:40]
        repo = (item.repository or "")[:20]
        sub = (item.submitter or "")[:15]
        click.echo(
            f"{item.review_request_id:>7}  "
            f"{item.diff_revision:>4}  "
            f"{item.status.value:<12}  "
            f"{repo:<20}  "
            f"{sub:<15}  "
            f"{summary}"
        )


@queue.command("set")
@click.argument("rr_ids", nargs=-1, required=True, type=int)
@click.option(
    "--status",
    required=True,
    type=click.Choice([s.value for s in QueueStatus]),
    help="Target status.",
)
@click.pass_context
def set_status(ctx: click.Context, rr_ids: tuple[int, ...], status: str) -> None:
    """Set the status of one or more queue items."""
    config = get_config(ctx)
    queue_db = _get_queue_db(config)
    new_status = QueueStatus(status)

    for rr_id in rr_ids:
        try:
            prev = queue_db.update_status(rr_id, new_status)
            click.echo(f"r/{rr_id}: {prev.value} -> {new_status.value}")
        except ValueError as e:
            click.echo(f"r/{rr_id}: {e}", err=True)


@queue.command()
@click.option("--count", default=5, help="Number of items to process.")
@click.option("--method", default="llm", type=click.Choice(["llm"]), help="Analysis method.")
@click.option("--model", "model_name", help="Override model from config.")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without doing it.")
@click.option("--fake-review", is_flag=True, help="Use mock LLM responses.")
@click.option("--submit/--no-submit", default=False, help="Submit reviews to RB (default: no).")
@click.pass_context
def process(
    ctx: click.Context,
    count: int,
    method: str,
    model_name: str | None,
    dry_run: bool,
    fake_review: bool,
    submit: bool,
) -> None:
    """Process queued items with status=next.

    Picks up to COUNT items, runs analysis, and records results.
    By default does NOT submit to Review Board.
    """
    config = get_config(ctx)
    queue_db = _get_queue_db(config)

    # Crash recovery: reset stale in_progress items
    reset_count = queue_db.reset_stale_in_progress()
    if reset_count > 0:
        click.echo(f"Reset {reset_count} stale in_progress item(s) to next.")

    items = queue_db.pick_next(count)
    if not items:
        click.echo("No items with status=next to process.")
        return

    click.echo(f"Processing {len(items)} item(s)...\n")

    if dry_run:
        for item in items:
            click.echo(
                f"  [DRY RUN] Would process r/{item.review_request_id} "
                f"(diff {item.diff_revision}, repo: {item.repository})"
            )
        return

    # Set up RB client, repo manager, analyzer
    from ..cli.analyze import _save_to_review_db, create_mock_review, run_analysis
    from ..db import ReviewDatabase
    from ..git import RepoManager
    from ..models import PendingReview
    from ..reviewers import Analyzer
    from ..rr.rb_client import ReviewBoardClient

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
    review_db = ReviewDatabase(config.review_db.resolved_path)

    analyzer = None
    if not fake_review:
        model = model_name or config.llm.model
        analyzer = Analyzer(
            api_key=config.llm.api_key,
            model=model,
            max_tokens=config.llm.max_tokens,
            temperature=config.llm.temperature,
            provider=config.llm.provider,
            base_url=config.llm.base_url,
            site_url=config.llm.site_url,
            site_name=config.llm.site_name or "BB Review",
        )

    succeeded = 0
    failed = 0

    for item in items:
        rr_id = item.review_request_id
        click.echo(f"Processing r/{rr_id} (diff {item.diff_revision})...")

        try:
            queue_db.mark_in_progress(rr_id)

            # Build PendingReview from queue item
            pending = PendingReview(
                review_request_id=rr_id,
                repository=item.repository or "unknown",
                submitter=item.submitter or "unknown",
                summary=item.summary or "",
                diff_revision=item.diff_revision,
                base_commit=item.base_commit,
                branch=item.branch,
            )

            # Get diff
            diff_info = rb_client.get_diff(rr_id, item.diff_revision)

            # Get repo config
            repo_config = repo_manager.get_repo_by_rb_name(pending.repository)
            if repo_config is None:
                raise RuntimeError(f"Repository not configured: {pending.repository}")

            if fake_review:
                result = create_mock_review(rr_id, diff_info.diff_revision)
                click.echo("  [FAKE] Using mock LLM response")
            else:
                # Checkout and analyze
                with repo_manager.checkout_context(
                    repo_config.name,
                    base_commit=pending.base_commit,
                    branch=pending.branch,
                    target_commit=diff_info.target_commit_id,
                    patch=diff_info.raw_diff,
                ) as (repo_path, _used_target):
                    result = run_analysis(
                        rr_id,
                        diff_info,
                        repo_path,
                        repo_config.name,
                        repo_manager,
                        analyzer,
                        config,
                    )

            click.echo(f"  Found {result.issue_count} issue(s)")

            # Save to review DB
            _save_to_review_db(
                config=config,
                result=result,
                repository=pending.repository,
                diff_info=diff_info,
                rr_summary=pending.summary,
                model=model_name or config.llm.model,
                fake=fake_review,
            )

            # Get the analysis_id from the DB
            analysis = review_db.get_analysis_by_rr(rr_id, item.diff_revision)
            analysis_id = analysis.id if analysis else None

            queue_db.mark_done(rr_id, analysis_id)
            click.echo(f"  Done (analysis_id={analysis_id})")
            succeeded += 1

            # Submit if requested
            if submit and not fake_review and analysis:
                _submit_review(rb_client, config, analysis, result)

        except Exception as e:
            logger.exception(f"Failed to process r/{rr_id}")
            queue_db.mark_failed(rr_id, str(e))
            click.echo(f"  FAILED: {e}", err=True)
            failed += 1

    click.echo(f"\nDone: {succeeded} succeeded, {failed} failed")


def _submit_review(rb_client, config, analysis, result) -> None:
    """Submit a review to Review Board."""
    from ..rr.rb_commenter import format_comments_for_rb

    comments = format_comments_for_rb(result)
    body_top = analysis.summary or result.summary

    try:
        rb_client.post_review(
            review_request_id=result.review_request_id,
            body_top=body_top,
            comments=comments,
            publish=True,
        )
        click.echo("  Submitted to RB")
    except Exception as e:
        click.echo(f"  Submit failed: {e}", err=True)


@queue.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Show queue statistics."""
    config = get_config(ctx)
    queue_db = _get_queue_db(config)

    st = queue_db.get_stats()

    click.echo("Queue Statistics:")
    click.echo(f"  Total: {st.get('total', 0)}")

    for status in QueueStatus:
        count = st.get(status.value, 0)
        if count > 0:
            click.echo(f"  {status.value}: {count}")


@queue.command()
@click.argument("rr_id", type=int)
@click.pass_context
def show(ctx: click.Context, rr_id: int) -> None:
    """Show details of a queue item."""
    config = get_config(ctx)
    queue_db = _get_queue_db(config)

    item = queue_db.get(rr_id)
    if item is None:
        click.echo(f"Queue item r/{rr_id} not found.", err=True)
        sys.exit(1)

    click.echo(f"Queue Item: r/{item.review_request_id}")
    click.echo(f"  Status:       {item.status.value}")
    click.echo(f"  Diff:         {item.diff_revision}")
    click.echo(f"  Repository:   {item.repository or 'unknown'}")
    click.echo(f"  Submitter:    {item.submitter or 'unknown'}")
    click.echo(f"  Summary:      {item.summary or ''}")
    click.echo(f"  Branch:       {item.branch or ''}")
    click.echo(f"  Base Commit:  {item.base_commit or ''}")
    click.echo(f"  Synced At:    {item.synced_at or ''}")
    click.echo(f"  Updated At:   {item.updated_at or ''}")

    if item.analysis_id is not None:
        click.echo(f"  Analysis ID:  {item.analysis_id}")
    if item.error_message:
        click.echo(f"  Error:        {item.error_message}")
