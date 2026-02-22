"""Dump RB review comments as markdown with source context."""

import logging
from pathlib import Path
import sys

import click

from ..git import RepoManager
from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher
from ..triage.models import RBComment
from . import get_config, main
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


@main.command()
@click.argument("review_id", type=REVIEW_ID)
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file path")
@click.option("--context-lines", type=int, default=15, help="Lines of context around each comment")
@click.option("--diff-revision", type=int, default=None, help="Specific diff revision to use")
@click.pass_context
def comments(
    ctx: click.Context,
    review_id: int,
    output: Path | None,
    context_lines: int,
    diff_revision: int | None,
) -> None:
    """Dump review comments as markdown with source context.

    Fetches all comments on REVIEW_ID, resolves source context from the
    local repo, and writes a markdown file suitable for feeding into
    another agent.

    REVIEW_ID can be a number or a Review Board URL.

    Examples:
        bb-review comments 18128
        bb-review comments 18128 -o my_comments.md
        bb-review comments 18128 --context-lines 30
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    click.echo(f"Fetching comments for review request #{review_id}...")

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

        fetcher = RBCommentFetcher(rb_client, config.reviewboard.bot_username)
        all_comments = fetcher.fetch_all_comments(review_id)

        if not all_comments:
            click.echo("No comments found on this review request.")
            return

        click.echo(f"  Found {len(all_comments)} comments")

        diff_info = rb_client.get_diff(review_id, diff_revision)
        repo_info = rb_client.get_repository_info(review_id)
        repo_name = repo_info.get("name", "unknown")

        click.echo(f"  Repository: {repo_name}")
        click.echo(f"  Diff revision: {diff_info.diff_revision}")

        # Resolve source context for inline comments
        repo_manager = RepoManager(config.get_all_repos())
        file_contexts = _get_comment_contexts(
            repo_manager,
            repo_name,
            all_comments,
            context_lines,
        )

        md = _render_comments_md(
            review_id,
            repo_name,
            diff_info.diff_revision,
            all_comments,
            file_contexts,
        )

        out_path = output or Path(f"comments_{review_id}.md")
        out_path.write_text(md)
        click.echo(f"  Written to {out_path}")

    except Exception as e:
        logger.exception("Comments export failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _get_comment_contexts(
    repo_manager: RepoManager,
    repo_name: str,
    comments: list[RBComment],
    context_lines: int,
) -> dict[tuple[str, int], str]:
    """Get source context for each inline comment.

    Returns a dict keyed by (file_path, line_number) -> context string.
    """
    repo_config = repo_manager.get_repo_by_rb_name(repo_name)
    if repo_config is None:
        return {}

    contexts: dict[tuple[str, int], str] = {}
    for c in comments:
        if not c.file_path or not c.line_number:
            continue
        key = (c.file_path, c.line_number)
        if key in contexts:
            continue
        ctx = repo_manager.get_file_context(
            repo_config.name,
            c.file_path,
            c.line_number,
            c.line_number,
            context_lines=context_lines,
        )
        if ctx:
            contexts[key] = ctx

    return contexts


def _render_comments_md(
    rr_id: int,
    repo_name: str,
    diff_revision: int,
    comments: list[RBComment],
    file_contexts: dict[tuple[str, int], str],
) -> str:
    """Render comments as markdown."""
    lines: list[str] = []
    lines.append(f"# Review Comments for r/{rr_id}")
    lines.append("")
    lines.append(f"Repository: {repo_name} | Diff revision: {diff_revision}")
    lines.append("")

    # Split into inline and body comments
    inline = [c for c in comments if c.file_path and not c.is_body_comment]
    body = [c for c in comments if c.is_body_comment]

    for c in inline:
        loc = f"{c.file_path}:{c.line_number}" if c.line_number else c.file_path
        lines.append(f"## {loc} -- {c.reviewer}")
        lines.append("")

        key = (c.file_path, c.line_number) if c.file_path and c.line_number else None
        ctx = file_contexts.get(key) if key else None
        if ctx:
            lines.append("### Source Context")
            lines.append("```")
            lines.append(ctx)
            lines.append("```")
            lines.append("")

        lines.append("### Comment")
        lines.append(c.text)
        lines.append("")
        lines.append("---")
        lines.append("")

    for c in body:
        lines.append(f"## General comment -- {c.reviewer}")
        lines.append("")
        lines.append("### Comment")
        lines.append(c.text)
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
