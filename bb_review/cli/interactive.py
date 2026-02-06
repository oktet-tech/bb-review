"""Interactive review management command for BB Review CLI."""

import logging
import sys

import click

from ..db import ReviewDatabase
from . import get_config, main


logger = logging.getLogger(__name__)


def get_review_db(ctx: click.Context) -> ReviewDatabase:
    """Get the ReviewDatabase instance, ensuring it's enabled in config."""
    config = get_config(ctx)

    if not config.review_db.enabled:
        click.echo(
            "Error: Reviews database is not enabled.\n"
            "Add the following to your config.yaml:\n\n"
            "review_db:\n"
            "  enabled: true\n"
            "  path: ~/.bb_review/reviews.db",
            err=True,
        )
        sys.exit(1)

    return ReviewDatabase(config.review_db.resolved_path)


def _suppress_console_logging() -> list[logging.StreamHandler]:
    """Remove stream handlers from root logger, return them for later restore."""
    root_logger = logging.getLogger()
    stream_handlers = [h for h in root_logger.handlers if isinstance(h, logging.StreamHandler)]
    for handler in stream_handlers:
        root_logger.removeHandler(handler)
    return stream_handlers


def _restore_console_logging(handlers: list[logging.StreamHandler]) -> None:
    """Re-add stream handlers to root logger."""
    root_logger = logging.getLogger()
    for handler in handlers:
        root_logger.addHandler(handler)


@main.command("interactive")
@click.option("--rr", "review_request_id", type=int, help="Filter by review request ID")
@click.option("--repo", "repository", help="Filter by repository name")
@click.option(
    "--status",
    type=click.Choice(["all", "draft", "submitted", "obsolete", "invalid"]),
    default="draft",
    help="Filter by analysis status (default: draft, use 'all' for no filter)",
)
@click.option("--chain", "chain_id", help="Filter by chain ID")
@click.option("--limit", "-n", default=50, type=int, help="Maximum number of results")
@click.option("--output", "-o", type=click.Path(), help="Output file path for export")
@click.option("--queue", is_flag=True, default=False, help="Start on queue tab (backward compat)")
@click.option(
    "--tab",
    type=click.Choice(["queue", "reviews", "work"]),
    default=None,
    help="Which tab to start on (default: queue)",
)
@click.option(
    "--queue-status",
    type=click.Choice(["all", "active", "todo", "next", "ignore", "in_progress", "done", "failed"]),
    default="active",
    help="Filter queue by status (default: active = exclude done/ignore, 'all' for everything)",
)
@click.pass_context
def interactive(
    ctx: click.Context,
    review_request_id: int | None,
    repository: str | None,
    status: str | None,
    chain_id: str | None,
    limit: int,
    output: str | None,
    queue: bool,
    tab: str | None,
    queue_status: str | None,
) -> None:
    """Interactive review management with TUI.

    Opens a unified TUI with tabbed Queue and Reviews panes.
    Use Tab to switch between panes, L to toggle the log panel.

    Queue pane:
    - S to sync from Review Board, R to process next items
    - Space to select, N/I/F/D to set status, X for action picker

    Reviews pane:
    - Space to select, Enter to open, X for actions, P to export

    Examples:
        bb-review interactive                    # Unified TUI, queue tab
        bb-review interactive --tab reviews      # Start on reviews tab
        bb-review interactive --queue            # Same as --tab queue
        bb-review interactive --rr 42738        # Filter reviews by RR
        bb-review interactive --repo te-dev     # Filter by repository
        bb-review interactive --status all      # Show all analysis statuses
    """
    # Resolve initial tab: --queue flag is alias for --tab queue
    initial_tab = tab or ("queue" if queue else "queue")

    _run_unified_tui(
        ctx,
        review_request_id=review_request_id,
        repository=repository,
        status=status,
        chain_id=chain_id,
        limit=limit,
        output=output,
        queue_status=queue_status,
        initial_tab=initial_tab,
    )


def _run_unified_tui(
    ctx: click.Context,
    *,
    review_request_id: int | None,
    repository: str | None,
    status: str | None,
    chain_id: str | None,
    limit: int,
    output: str | None,
    queue_status: str | None,
    initial_tab: str,
) -> None:
    """Launch the unified TUI."""
    from ..db import QueueDatabase, QueueStatus
    from ..ui import UnifiedApp

    config = get_config(ctx)

    if not config.review_db.enabled:
        click.echo("Error: Reviews database is not enabled.", err=True)
        sys.exit(1)

    # -- Queue data --
    queue_db = QueueDatabase(config.review_db.resolved_path)

    q_status_filter = None
    q_exclude_statuses = None
    if queue_status == "active":
        q_exclude_statuses = [QueueStatus.DONE, QueueStatus.IGNORE]
    elif queue_status and queue_status != "all":
        q_status_filter = QueueStatus(queue_status)

    queue_items = queue_db.list_items(
        status=q_status_filter,
        repository=repository,
        limit=limit,
        exclude_statuses=q_exclude_statuses,
    )

    # -- Reviews data --
    review_db = ReviewDatabase(config.review_db.resolved_path)
    status_filter = None if status == "all" else status

    analyses = review_db.list_analyses(
        review_request_id=review_request_id,
        repository=repository,
        status=status_filter,
        chain_id=chain_id,
        limit=limit,
    )

    # -- Launch --
    handlers = _suppress_console_logging()

    app = UnifiedApp(
        queue_items=queue_items,
        queue_db=queue_db,
        queue_filter_status=q_status_filter,
        queue_exclude_statuses=q_exclude_statuses,
        queue_filter_repo=repository,
        queue_filter_limit=limit,
        analyses=analyses,
        review_db=review_db,
        config=config,
        output_path=output,
        review_filter_rr_id=review_request_id,
        review_filter_repo=repository,
        review_filter_status=status_filter,
        review_filter_chain_id=chain_id,
        review_filter_limit=limit,
        initial_tab=initial_tab,
    )
    app.run()

    _restore_console_logging(handlers)
