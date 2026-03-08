"""Resolve review comments by updating issue status on Review Board."""

import json
import logging
from pathlib import Path
import sys

import click

from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher
from ..triage.models import CommentResolution
from . import get_config, main
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)

# Map agent-facing status names to RB API values
STATUS_MAP = {
    "fixed": "resolved",
    "dropped": "dropped",
}

VALID_STATUSES = set(STATUS_MAP.keys())


def _parse_resolutions(raw: str) -> list[CommentResolution]:
    """Parse JSON input into CommentResolution list."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}") from e

    if not isinstance(data, list):
        raise click.BadParameter("Expected a JSON array")

    resolutions = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise click.BadParameter(f"Item {i}: expected an object")
        if "comment_id" not in item:
            raise click.BadParameter(f"Item {i}: missing comment_id")
        if "status" not in item:
            raise click.BadParameter(f"Item {i}: missing status")
        if item["status"] not in VALID_STATUSES:
            raise click.BadParameter(f"Item {i}: status must be one of {VALID_STATUSES}")

        resolutions.append(
            CommentResolution(
                comment_id=item["comment_id"],
                status=item["status"],
                message=item.get("message", ""),
            )
        )

    return resolutions


def _build_comment_map(
    rb_client: ReviewBoardClient,
    bot_username: str,
    rr_id: int,
) -> dict[int, int]:
    """Build comment_id -> review_id map by fetching all comments."""
    fetcher = RBCommentFetcher(rb_client, bot_username)
    all_comments = fetcher.fetch_all_comments(rr_id, include_bot=True)
    return {c.comment_id: c.review_id for c in all_comments if not c.is_body_comment}


@main.command()
@click.argument("review_id", type=REVIEW_ID)
@click.option("-f", "--file", "input_file", type=click.Path(exists=True, path_type=Path), help="JSON file")
@click.option("--dry-run", is_flag=True, help="Print plan without making changes")
@click.pass_context
def resolve(
    ctx: click.Context,
    review_id: int,
    input_file: Path | None,
    dry_run: bool,
) -> None:
    """Resolve review comments by updating issue status on Review Board.

    Reads a JSON array of resolutions from stdin or a file (-f).
    Each entry needs comment_id and status ('fixed' or 'dropped'),
    with an optional message to post as a reply.

    REVIEW_ID can be a number or a Review Board URL.

    Examples:
        echo '[{"comment_id": 123, "status": "fixed"}]' | bb-review resolve 18128
        bb-review resolve 18128 -f resolutions.json
        bb-review resolve 18128 -f resolutions.json --dry-run
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    # Read input
    if input_file:
        raw = input_file.read_text()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        click.echo("Error: Provide JSON via stdin or -f FILE", err=True)
        sys.exit(1)

    resolutions = _parse_resolutions(raw)
    if not resolutions:
        click.echo("No resolutions to process.")
        return

    click.echo(f"Resolving {len(resolutions)} comments on review request #{review_id}...")

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

        # Build comment_id -> review_id lookup
        comment_map = _build_comment_map(rb_client, config.reviewboard.bot_username, review_id)

        ok_count = 0
        skip_count = 0
        fail_count = 0

        for res in resolutions:
            rv_id = comment_map.get(res.comment_id)
            if rv_id is None:
                click.echo(f"  SKIP c:{res.comment_id} -- not found in review comments")
                skip_count += 1
                continue

            rb_status = STATUS_MAP[res.status]

            if dry_run:
                msg_part = f' reply="{res.message}"' if res.message else ""
                click.echo(f"  DRY-RUN c:{res.comment_id} -> {rb_status}{msg_part}")
                ok_count += 1
                continue

            try:
                rb_client.update_issue_status(review_id, rv_id, res.comment_id, rb_status)

                if res.message:
                    reply = rb_client.post_reply(review_id, rv_id, body_top="")
                    rb_client.post_diff_comment_reply(
                        review_id,
                        rv_id,
                        reply["id"],
                        res.comment_id,
                        res.message,
                    )
                    rb_client.publish_reply(review_id, rv_id, reply["id"])

                click.echo(f"  OK c:{res.comment_id} -> {rb_status}")
                ok_count += 1
            except Exception as e:
                click.echo(f"  FAIL c:{res.comment_id} -- {e}")
                fail_count += 1

        # Summary
        click.echo("")
        parts = [f"{ok_count} resolved"]
        if skip_count:
            parts.append(f"{skip_count} skipped")
        if fail_count:
            parts.append(f"{fail_count} failed")
        prefix = "DRY-RUN: " if dry_run else ""
        click.echo(f"{prefix}Done: {', '.join(parts)}")

    except Exception as e:
        logger.exception("Resolve failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
