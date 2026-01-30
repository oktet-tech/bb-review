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
@click.option(
    "--publish/--no-publish",
    default=False,
    help="Publish review (visible to others). Default: --no-publish (draft mode)",
)
@click.pass_context
def submit_cmd(ctx: click.Context, json_file: Path, dry_run: bool, publish: bool) -> None:
    """Submit a review JSON file to ReviewBoard.

    This allows a workflow where you can:

    \b
    1. Run analysis to generate a JSON file (bb-review analyze 42738 -O)
    2. Review and edit the JSON file as needed
    3. Submit as draft to ReviewBoard (bb-review submit review.json)
    4. Verify in RB UI, then publish (bb-review submit review.json --publish)

    By default, reviews are submitted as DRAFTS (only visible to you).
    Use --publish to make the review visible to everyone.

    Example:

    \b
        bb-review analyze 42738 -O          # Generate review_42738.json
        bb-review submit review_42738.json  # Submit as draft
        bb-review submit review_42738.json --publish  # Publish to everyone
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
        click.echo(f"  Publish: {'Yes (visible to all)' if publish else 'No (draft only)'}")

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
            publish=publish,
        )

        if publish:
            click.echo(f"\nPublished review (ID: {review_posted['id']})")
            click.echo(f"  - {len(comments)} inline comments")
            click.echo("  - Review is now visible to everyone")
        else:
            click.echo(f"\nPosted review as draft (ID: {review_posted['id']})")
            click.echo(f"  - {len(comments)} inline comments")
            click.echo("  - Review is only visible to you")
            click.echo(f"\nTo publish: bb-review submit {json_file} --publish")
            click.echo("Or publish manually in Review Board UI")

    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON in {json_file}: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.exception("Failed to submit review")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
