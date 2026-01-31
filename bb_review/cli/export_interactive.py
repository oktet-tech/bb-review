"""Interactive export command for BB Review CLI."""

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


@main.command("export-interactive")
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
@click.option("--output", "-o", type=click.Path(), help="Output file path")
@click.pass_context
def export_interactive(
    ctx: click.Context,
    review_request_id: int | None,
    repository: str | None,
    status: str | None,
    chain_id: str | None,
    limit: int,
    output: str | None,
) -> None:
    """Interactive export of reviews with comment selection.

    Opens a TUI to:
    - Browse and select analyses to export
    - Pick individual comments to include
    - Edit comments before export
    - Export to JSON for submission

    Examples:
        bb-review export-interactive                    # Export draft analyses
        bb-review export-interactive --rr 42738        # Export specific RR
        bb-review export-interactive --repo te-dev     # Export repo analyses
        bb-review export-interactive -o review.json    # Specify output file
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

    # Run the interactive app
    app = ExportApp(analyses=analyses, db=review_db, output_path=output)
    app.run()
