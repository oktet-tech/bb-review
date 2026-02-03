"""Interactive review management command for BB Review CLI."""

import logging
import sys

import click

from ..db import ReviewDatabase
from ..ui import ExportApp
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
@click.option("--queue", is_flag=True, default=False, help="Open queue triage view instead")
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
    queue_status: str | None,
) -> None:
    """Interactive review management with TUI.

    Opens a TUI to browse and manage stored analyses:
    - Select analyses with Space, batch export with P
    - Press Enter on a row for actions: Export, Delete, Mark as...
    - Pick individual comments to include in export
    - Edit comments before export

    Use --queue to open the queue triage view instead.

    Examples:
        bb-review interactive                    # Browse draft analyses
        bb-review interactive --rr 42738        # Filter by specific RR
        bb-review interactive --repo te-dev     # Filter by repository
        bb-review interactive --status submitted # Browse submitted analyses
        bb-review interactive --status all      # Browse all analyses
        bb-review interactive -o review.json    # Specify export output file
        bb-review interactive --queue            # Queue triage (excludes done/ignore)
        bb-review interactive --queue --queue-status all   # All queue items
        bb-review interactive --queue --queue-status todo  # Only todo items
    """
    if queue:
        _run_queue_tui(ctx, repository, queue_status, limit)
    else:
        _run_export_tui(ctx, review_request_id, repository, status, chain_id, limit, output)


def _run_queue_tui(
    ctx: click.Context,
    repository: str | None,
    queue_status: str | None,
    limit: int,
) -> None:
    """Launch the queue triage TUI."""
    from ..db import QueueDatabase, QueueStatus
    from ..ui import QueueApp

    config = get_config(ctx)

    if not config.review_db.enabled:
        click.echo("Error: Reviews database is not enabled.", err=True)
        sys.exit(1)

    queue_db = QueueDatabase(config.review_db.resolved_path)

    status_filter = None
    exclude_statuses = None
    if queue_status == "active":
        exclude_statuses = [QueueStatus.DONE, QueueStatus.IGNORE]
    elif queue_status and queue_status != "all":
        status_filter = QueueStatus(queue_status)

    items = queue_db.list_items(
        status=status_filter,
        repository=repository,
        limit=limit,
        exclude_statuses=exclude_statuses,
    )

    if not items:
        click.echo("No queue items found matching the filter.")
        return

    handlers = _suppress_console_logging()

    app = QueueApp(
        items=items,
        queue_db=queue_db,
        filter_status=status_filter,
        exclude_statuses=exclude_statuses,
        filter_repo=repository,
        filter_limit=limit,
    )
    app.run()

    _restore_console_logging(handlers)


def _run_export_tui(
    ctx: click.Context,
    review_request_id: int | None,
    repository: str | None,
    status: str | None,
    chain_id: str | None,
    limit: int,
    output: str | None,
) -> None:
    """Launch the export/analysis TUI."""
    review_db = get_review_db(ctx)

    # Convert "all" to None for no status filter
    status_filter = None if status == "all" else status

    analyses = review_db.list_analyses(
        review_request_id=review_request_id,
        repository=repository,
        status=status_filter,
        chain_id=chain_id,
        limit=limit,
    )

    if not analyses:
        click.echo("No analyses found matching the filter.")
        return

    handlers = _suppress_console_logging()

    config = get_config(ctx)
    app = ExportApp(
        analyses=analyses,
        db=review_db,
        config=config,
        output_path=output,
        filter_rr_id=review_request_id,
        filter_repo=repository,
        filter_status=status_filter,
        filter_chain_id=chain_id,
        filter_limit=limit,
    )
    app.run()

    _restore_console_logging(handlers)
