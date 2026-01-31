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


@main.command("interactive")
@click.option("--rr", "review_request_id", type=int, help="Filter by review request ID")
@click.option("--repo", "repository", help="Filter by repository name")
@click.option(
    "--status",
    type=click.Choice(["draft", "submitted", "obsolete", "invalid"]),
    default="draft",
    help="Filter by status (default: draft)",
)
@click.option("--chain", "chain_id", help="Filter by chain ID")
@click.option("--limit", "-n", default=50, type=int, help="Maximum number of results")
@click.option("--output", "-o", type=click.Path(), help="Output file path for export")
@click.pass_context
def interactive(
    ctx: click.Context,
    review_request_id: int | None,
    repository: str | None,
    status: str | None,
    chain_id: str | None,
    limit: int,
    output: str | None,
) -> None:
    """Interactive review management with TUI.

    Opens a TUI to browse and manage stored analyses:
    - Select analyses with Space, batch export with P
    - Press Enter on a row for actions: Export, Delete, Mark as...
    - Pick individual comments to include in export
    - Edit comments before export

    Examples:
        bb-review interactive                    # Browse draft analyses
        bb-review interactive --rr 42738        # Filter by specific RR
        bb-review interactive --repo te-dev     # Filter by repository
        bb-review interactive --status submitted # Browse submitted analyses
        bb-review interactive -o review.json    # Specify export output file
    """
    review_db = get_review_db(ctx)

    # Fetch analyses matching the filter
    analyses = review_db.list_analyses(
        review_request_id=review_request_id,
        repository=repository,
        status=status,
        chain_id=chain_id,
        limit=limit,
    )

    if not analyses:
        click.echo("No analyses found matching the filter.")
        return

    # Suppress console logging during TUI (logs go to file only)
    root_logger = logging.getLogger()
    stream_handlers = [h for h in root_logger.handlers if isinstance(h, logging.StreamHandler)]
    for handler in stream_handlers:
        root_logger.removeHandler(handler)

    # Run the interactive app with filter params for refresh
    config = get_config(ctx)
    app = ExportApp(
        analyses=analyses,
        db=review_db,
        config=config,
        output_path=output,
        filter_rr_id=review_request_id,
        filter_repo=repository,
        filter_status=status,
        filter_chain_id=chain_id,
        filter_limit=limit,
    )
    app.run()

    # Restore stream handlers after TUI exits
    for handler in stream_handlers:
        root_logger.addHandler(handler)
