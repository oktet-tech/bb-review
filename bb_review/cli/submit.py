"""Submit command for BB Review CLI."""

import json
import logging
from pathlib import Path
import sys

import click

from ..rr import ReviewBoardClient
from . import get_config, main


logger = logging.getLogger(__name__)


def _load_and_validate(json_file: Path) -> dict:
    """Load a review JSON file and validate required fields. Raises on error."""
    data = json.loads(json_file.read_text())

    required_fields = ["review_request_id", "body_top", "comments"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    for i, comment in enumerate(data["comments"]):
        if "file_path" not in comment or "line_number" not in comment or "text" not in comment:
            raise ValueError(f"Comment {i} missing required fields (file_path, line_number, text)")

    return data


def _print_review_summary(data: dict, json_file: Path, publish: bool) -> None:
    """Print a summary of a loaded review."""
    ship_it = data.get("ship_it", False)
    click.echo(f"  Review request: #{data['review_request_id']}")
    click.echo(f"  Comments: {len(data['comments'])}")
    click.echo(f"  Ship It: {'Yes' if ship_it else 'No'}")
    click.echo(f"  Publish: {'Yes (visible to all)' if publish else 'No (draft only)'}")


def _print_dry_run(data: dict) -> None:
    """Print dry-run details for a single review."""
    body_top = data["body_top"]
    comments = data["comments"]
    click.echo("\n[Dry run - would post the following review]")
    click.echo("\n--- Body Top ---")
    click.echo(body_top[:500] + "..." if len(body_top) > 500 else body_top)
    if comments:
        click.echo("\n--- Inline Comments ---")
        for c in comments:
            click.echo(f"  {c['file_path']}:{c['line_number']}")
            preview = c["text"][:100] + "..." if len(c["text"]) > 100 else c["text"]
            click.echo(f"    {preview}")


def _submit_one(
    rb_client: ReviewBoardClient,
    data: dict,
    json_file: Path,
    publish: bool,
    config,
) -> None:
    """Submit a single review and update the DB."""
    review_request_id = data["review_request_id"]
    comments = data["comments"]
    ship_it = data.get("ship_it", False)

    review_posted = rb_client.post_review(
        review_request_id=review_request_id,
        body_top=data["body_top"],
        comments=comments,
        ship_it=ship_it,
        publish=publish,
    )

    if publish:
        click.echo(f"  Published review (ID: {review_posted['id']})")
        click.echo(f"    {len(comments)} inline comments, visible to everyone")
    else:
        click.echo(f"  Posted review as draft (ID: {review_posted['id']})")
        click.echo(f"    {len(comments)} inline comments, visible to you only")

    if config.review_db.enabled:
        _update_review_db_status(
            config=config,
            data=data,
            review_request_id=review_request_id,
        )


@main.command("submit")
@click.argument("json_files", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Validate and show what would be posted")
@click.option(
    "--publish/--no-publish",
    default=False,
    help="Publish review (visible to others). Default: --no-publish (draft mode)",
)
@click.option(
    "--ship-it/--no-ship-it",
    default=None,
    help="Override ship-it flag from JSON data",
)
@click.pass_context
def submit_cmd(
    ctx: click.Context,
    json_files: tuple[Path, ...],
    dry_run: bool,
    publish: bool,
    ship_it: bool | None,
) -> None:
    """Submit review JSON file(s) to ReviewBoard.

    Accepts one or more JSON files. When multiple files are given, all are
    validated upfront before any submission begins.

    This allows a workflow where you can:

    \b
    1. Run analysis to generate JSON files (bb-review analyze 42738 -O)
    2. Review and edit the JSON files as needed
    3. Submit as draft to ReviewBoard (bb-review submit review.json)
    4. Verify in RB UI, then publish (bb-review submit review.json --publish)

    By default, reviews are submitted as DRAFTS (only visible to you).
    Use --publish to make the review visible to everyone.

    Example:

    \b
        bb-review submit review_42738.json                          # Single file
        bb-review submit review_42738.json review_42739.json        # Batch
        bb-review submit review_*.json --publish                    # Glob + publish
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    batch = len(json_files) > 1

    # Phase 1: validate all files upfront
    loaded: list[tuple[Path, dict]] = []
    for json_file in json_files:
        try:
            data = _load_and_validate(json_file)
        except json.JSONDecodeError as e:
            click.echo(f"Error: Invalid JSON in {json_file}: {e}", err=True)
            sys.exit(1)
        except ValueError as e:
            click.echo(f"Error in {json_file}: {e}", err=True)
            sys.exit(1)

        # Apply --ship-it override
        if ship_it is not None:
            data["ship_it"] = ship_it

        loaded.append((json_file, data))

    if batch:
        click.echo(f"Validated {len(loaded)} review files")

    # Phase 2: show summaries / dry-run
    for json_file, data in loaded:
        click.echo(f"\n{json_file}:")
        _print_review_summary(data, json_file, publish)
        if dry_run:
            _print_dry_run(data)

    if dry_run:
        return

    # Phase 3: submit
    try:
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            bot_username=config.reviewboard.bot_username,
            api_token=config.reviewboard.api_token,
            username=config.reviewboard.username,
            password=config.reviewboard.get_password(),
            use_kerberos=config.reviewboard.use_kerberos,
        )
        rb_client.connect()
    except Exception as e:
        logger.exception("Failed to connect to ReviewBoard")
        click.echo(f"Error connecting to ReviewBoard: {e}", err=True)
        sys.exit(1)

    succeeded = 0
    failed = 0
    for json_file, data in loaded:
        if batch:
            click.echo(f"\nSubmitting {json_file}...")
        try:
            _submit_one(rb_client, data, json_file, publish, config)
            succeeded += 1
        except Exception as e:
            logger.exception(f"Failed to submit {json_file}")
            click.echo(f"  Error: {e}", err=True)
            failed += 1

    # Summary
    if batch:
        click.echo(f"\nDone: {succeeded} submitted, {failed} failed")
    elif failed:
        sys.exit(1)
    elif not publish:
        json_file = loaded[0][0]
        click.echo(f"\nTo publish: bb-review submit {json_file} --publish")
        click.echo("Or publish manually in Review Board UI")


def _update_review_db_status(config, data: dict, review_request_id: int) -> None:
    """Update the review status in the database after submission."""
    from ..db import ReviewDatabase

    try:
        review_db = ReviewDatabase(config.review_db.resolved_path)

        # Try to find the analysis ID from metadata
        analysis_id = None
        if "metadata" in data and isinstance(data["metadata"], dict):
            analysis_id = data["metadata"].get("analysis_id")

        if analysis_id:
            review_db.mark_submitted(analysis_id)
            logger.debug(f"Marked analysis {analysis_id} as submitted")
        else:
            # Find the most recent draft analysis for this RR
            analyses = review_db.list_analyses(
                review_request_id=review_request_id,
                status="draft",
                limit=1,
            )
            if analyses:
                review_db.mark_submitted(analyses[0].id)
                logger.debug(f"Marked analysis {analyses[0].id} as submitted (found by RR ID)")
            else:
                logger.debug(f"No draft analysis found for RR {review_request_id}")

    except Exception as e:
        logger.warning(f"Failed to update review status in database: {e}")
