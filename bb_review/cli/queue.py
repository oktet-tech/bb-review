"""Queue CLI commands for review triage workflow."""

import json
import logging
from pathlib import Path
import sys

import click

from ..config import Config
from ..db.queue_db import QueueDatabase
from ..db.queue_models import QueueItem, QueueStatus
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
@click.option("--prune/--no-prune", default=True, help="Remove gone RRs from queue (default: prune).")
@click.pass_context
def sync(
    ctx: click.Context,
    days: int,
    limit: int,
    bot_only: bool,
    repository: str | None,
    submitter: str | None,
    prune: bool,
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
        prune=prune,
    )

    click.echo(
        f"Sync complete: {counts['total']} fetched, "
        f"{counts['inserted']} new, "
        f"{counts['updated']} reset, "
        f"{counts['skipped']} unchanged, "
        f"{counts['analyzed']} already analyzed"
    )
    if counts.get("pruned", 0) > 0:
        click.echo(f"Pruned {counts['pruned']} gone item(s) from queue")


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
@click.option("--count", default=None, type=int, help="Number of items to process (default: from config).")
@click.option(
    "--method",
    default=None,
    type=click.Choice(["llm", "opencode", "claude"]),
    help="Analysis method (default: from config).",
)
@click.option("--model", "model_name", help="Override model from config.")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without doing it.")
@click.option("--fake-review", is_flag=True, help="Use mock responses.")
@click.option("--submit/--no-submit", default=False, help="Submit reviews to RB (default: no).")
@click.option(
    "--fallback/--no-fallback",
    default=True,
    help="Fall back to patch-file mode if patch doesn't apply (default: fallback).",
)
@click.pass_context
def process(
    ctx: click.Context,
    count: int | None,
    method: str | None,
    model_name: str | None,
    dry_run: bool,
    fake_review: bool,
    submit: bool,
    fallback: bool,
) -> None:
    """Process queued items with status=next.

    Picks up to COUNT items, runs analysis, and records results.
    By default does NOT submit to Review Board.
    """
    config = get_config(ctx)
    queue_db = _get_queue_db(config)

    # Resolve from config defaults
    method = method or config.queue.method
    count = count or config.queue.count

    # Crash recovery: reset stale in_progress items
    reset_count = queue_db.reset_stale_in_progress()
    if reset_count > 0:
        click.echo(f"Reset {reset_count} stale in_progress item(s) to next.")

    items = queue_db.pick_next(count)
    if not items:
        click.echo("No items with status=next to process.")
        return

    click.echo(f"Processing {len(items)} item(s) with method={method}...\n")

    if dry_run:
        for item in items:
            click.echo(
                f"  [DRY RUN] Would process r/{item.review_request_id} "
                f"(diff {item.diff_revision}, repo: {item.repository})"
            )
        return

    # Check binary availability for agent methods before starting the loop
    if method == "opencode" and not fake_review:
        from ..reviewers import check_opencode_available

        available, msg = check_opencode_available(config.opencode.binary_path)
        if not available:
            click.echo(f"Error: {msg}", err=True)
            sys.exit(1)
    elif method == "claude" and not fake_review:
        from ..reviewers import check_claude_available

        available, msg = check_claude_available(config.claude_code.binary_path)
        if not available:
            click.echo(f"Error: {msg}", err=True)
            sys.exit(1)

    from ..db import ReviewDatabase
    from ..git import RepoManager
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

    # Map CLI method name to DB analysis_method
    analysis_method = "claude_code" if method == "claude" else method

    succeeded = 0
    skipped = 0
    failed = 0

    for item in items:
        rr_id = item.review_request_id
        click.echo(f"Processing r/{rr_id} (diff {item.diff_revision})...")

        try:
            # Skip if a real (non-fake) analysis already exists for this method
            if review_db.has_real_analysis(rr_id, item.diff_revision, analysis_method):
                existing = review_db.get_analysis_by_rr(rr_id, item.diff_revision)
                analysis_id = existing.id if existing else None
                queue_db.mark_done(rr_id, analysis_id)
                click.echo(f"  Skipped: already analyzed (analysis_id={analysis_id})")
                skipped += 1
                continue

            queue_db.mark_in_progress(rr_id)

            if method == "llm":
                _process_item_llm(
                    item,
                    config,
                    rb_client,
                    repo_manager,
                    review_db,
                    queue_db,
                    model_name,
                    fake_review,
                    submit,
                )
            else:
                _process_item_agent(
                    item,
                    method,
                    config,
                    rb_client,
                    repo_manager,
                    review_db,
                    queue_db,
                    model_name,
                    fake_review,
                    submit,
                    fallback,
                )

            succeeded += 1

        except Exception as e:
            logger.exception(f"Failed to process r/{rr_id}")
            queue_db.mark_failed(rr_id, str(e))
            click.echo(f"  FAILED: {e}", err=True)
            failed += 1

    click.echo(f"\nDone: {succeeded} succeeded, {skipped} skipped, {failed} failed")


def _process_item_llm(
    item: QueueItem,
    config: Config,
    rb_client,
    repo_manager,
    review_db,
    queue_db: QueueDatabase,
    model_name: str | None,
    fake_review: bool,
    submit: bool,
) -> None:
    """Process a single queue item using direct LLM analysis."""
    from ..cli.analyze import _save_to_review_db, create_mock_review, run_analysis
    from ..models import PendingReview
    from ..reviewers import Analyzer

    rr_id = item.review_request_id

    pending = PendingReview(
        review_request_id=rr_id,
        repository=item.repository or "unknown",
        submitter=item.submitter or "unknown",
        summary=item.summary or "",
        diff_revision=item.diff_revision,
        base_commit=item.base_commit,
        branch=item.branch,
    )

    diff_info = rb_client.get_diff(rr_id, item.diff_revision)

    repo_config = repo_manager.get_repo_by_rb_name(pending.repository)
    if repo_config is None:
        raise RuntimeError(f"Repository not configured: {pending.repository}")

    if fake_review:
        result = create_mock_review(rr_id, diff_info.diff_revision)
        click.echo("  [FAKE] Using mock LLM response")
    else:
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

    _save_to_review_db(
        config=config,
        result=result,
        repository=pending.repository,
        diff_info=diff_info,
        rr_summary=pending.summary,
        model=model_name or config.llm.model,
        fake=fake_review,
    )

    analysis = review_db.get_analysis_by_rr(rr_id, item.diff_revision)
    analysis_id = analysis.id if analysis else None

    queue_db.mark_done(rr_id, analysis_id)
    click.echo(f"  Done (analysis_id={analysis_id})")

    if submit and not fake_review and analysis:
        _submit_review(rb_client, config, analysis, result)


def _process_item_agent(
    item: QueueItem,
    method: str,
    config: Config,
    rb_client,
    repo_manager,
    review_db,
    queue_db: QueueDatabase,
    model_name: str | None,
    fake_review: bool,
    submit: bool,
    fallback: bool,
) -> None:
    """Process a single queue item using opencode or claude_code agent."""
    from ..reviewers import parse_opencode_output
    from ._review_runner import build_submission_data, create_mock_review_output, save_to_review_db

    rr_id = item.review_request_id
    repository = item.repository or "unknown"

    diff_info = rb_client.get_diff(rr_id, item.diff_revision)

    repo_config = repo_manager.get_repo_by_rb_name(repository)
    if repo_config is None:
        raise RuntimeError(f"Repository not configured: {repository}")

    # Resolve model and method-specific params
    if method == "opencode":
        model = model_name or config.opencode.model or "default"
        analysis_method = "opencode"
        method_label = "OpenCode"
    else:
        model = model_name or config.claude_code.model or "default"
        analysis_method = "claude_code"
        method_label = "Claude Code"

    with repo_manager.checkout_context(
        repo_config.name,
        base_commit=item.base_commit,
        branch=item.branch,
        target_commit=diff_info.target_commit_id,
        patch=diff_info.raw_diff,
        require_patch=not fallback,
    ) as (repo_path, used_target):
        if fake_review:
            analysis_text = create_mock_review_output(rr_id)
            click.echo("  [FAKE] Using mock response")
        else:
            summary = item.summary or ""
            analysis_text = _run_agent_review(
                method,
                rr_id,
                summary,
                diff_info.raw_diff,
                repo_path,
                repo_config,
                used_target,
                config,
                model_name,
            )

    parsed = parse_opencode_output(analysis_text)
    click.echo(f"  Found {len(parsed.issues)} issue(s)")

    output_data = build_submission_data(
        rr_id,
        analysis_text,
        parsed,
        model,
        rr_summary=item.summary,
        method_label=method_label,
    )

    # Write review file (auto-output)
    output_file = Path(f"review_{rr_id}.json")
    output_file.write_text(json.dumps(output_data, indent=2))
    click.echo(f"  Saved: {output_file}")

    save_to_review_db(
        config=config,
        review_id=rr_id,
        diff_revision=diff_info.diff_revision,
        repository=repo_config.name,
        parsed=parsed,
        model=model,
        analysis_method=analysis_method,
        rr_summary=item.summary,
        fake=fake_review,
        body_top=output_data.get("body_top"),
    )

    analysis = review_db.get_analysis_by_rr(rr_id, item.diff_revision)
    analysis_id = analysis.id if analysis else None

    queue_db.mark_done(rr_id, analysis_id)
    click.echo(f"  Done (analysis_id={analysis_id})")

    if submit and not fake_review and analysis:
        # For agent methods, build a ReviewResult-like object for _submit_review
        click.echo("  Submit not yet supported for agent methods", err=True)


def _run_agent_review(
    method: str,
    rr_id: int,
    summary: str,
    raw_diff: str,
    repo_path: Path,
    repo_config,
    at_reviewed_state: bool,
    config: Config,
    model_name: str | None,
) -> str:
    """Dispatch to the appropriate agent reviewer function."""
    if method == "opencode":
        from .opencode import run_opencode_for_review

        return run_opencode_for_review(
            review_id=rr_id,
            summary=summary,
            raw_diff=raw_diff,
            repo_path=repo_path,
            repo_config=repo_config,
            model=model_name or config.opencode.model,
            timeout=config.opencode.timeout,
            binary_path=config.opencode.binary_path,
            at_reviewed_state=at_reviewed_state,
        )
    else:
        from .claude_code import run_claude_for_review

        cc = config.claude_code
        return run_claude_for_review(
            review_id=rr_id,
            summary=summary,
            raw_diff=raw_diff,
            repo_path=repo_path,
            repo_config=repo_config,
            model=model_name or cc.model,
            timeout=cc.timeout,
            max_turns=cc.max_turns,
            binary_path=cc.binary_path,
            allowed_tools=cc.allowed_tools,
            at_reviewed_state=at_reviewed_state,
            mcp_config=Path(cc.mcp_config) if cc.mcp_config else None,
        )


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
