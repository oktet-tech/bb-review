"""Submit command for BB Review CLI."""

import json
import logging
from pathlib import Path
import sys

import click

from ..rr import ReviewBoardClient
from . import get_config, main


logger = logging.getLogger(__name__)


@main.command("submit")
@click.argument("json_file", type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Validate and show what would be posted")
@click.pass_context
def submit_cmd(ctx: click.Context, json_file: Path, dry_run: bool) -> None:
    """Submit a pre-edited review JSON file to ReviewBoard.

    This allows a workflow where you can:

    \b
    1. Run analysis in dry-run mode to generate a JSON file
    2. Review and edit the JSON file as needed
    3. Submit the edited review to ReviewBoard

    Example:

    \b
        bb-review opencode 42738 --dry-run -o review.json
        # Edit review.json as needed
        bb-review submit review.json
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    click.echo(f"Loading review from {json_file}...")

    try:
        # Load and validate JSON
        data = json.loads(json_file.read_text())

        # Validate required fields
        required_fields = ["review_request_id", "body_top", "comments"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            click.echo(f"Error: Missing required fields: {', '.join(missing)}", err=True)
            sys.exit(1)

        review_request_id = data["review_request_id"]
        body_top = data["body_top"]
        comments = data["comments"]
        ship_it = data.get("ship_it", False)

        # Validate comments structure
        for i, comment in enumerate(comments):
            if "file_path" not in comment or "line_number" not in comment or "text" not in comment:
                click.echo(
                    f"Error: Comment {i} missing required fields (file_path, line_number, text)",
                    err=True,
                )
                sys.exit(1)

        click.echo(f"  Review request: #{review_request_id}")
        click.echo(f"  Comments: {len(comments)}")
        click.echo(f"  Ship It: {'Yes' if ship_it else 'No'}")

        if dry_run:
            click.echo("\n[Dry run - would post the following review]")
            click.echo("\n--- Body Top ---")
            click.echo(body_top[:500] + "..." if len(body_top) > 500 else body_top)
            if comments:
                click.echo("\n--- Inline Comments ---")
                for c in comments:
                    click.echo(f"  {c['file_path']}:{c['line_number']}")
                    preview = c["text"][:100] + "..." if len(c["text"]) > 100 else c["text"]
                    click.echo(f"    {preview}")
            return

        # Initialize RB client and post
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            bot_username=config.reviewboard.bot_username,
            api_token=config.reviewboard.api_token,
            username=config.reviewboard.username,
            password=config.reviewboard.get_password(),
            use_kerberos=config.reviewboard.use_kerberos,
        )
        rb_client.connect()

        review_posted = rb_client.post_review(
            review_request_id=review_request_id,
            body_top=body_top,
            comments=comments,
            ship_it=ship_it,
        )
        click.echo(f"\nPosted review (ID: {review_posted})")
        click.echo(f"  - {len(comments)} inline comments")

    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON in {json_file}: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Failed to submit review")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
