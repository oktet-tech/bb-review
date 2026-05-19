"""Rules-mining commands: cache reviewer comments and draft repo rules."""

import logging
from pathlib import Path
import sys

import click

from ..config import Config
from ..db.mining_db import MiningDatabase
from ..git import RepoManager, RepoManagerError
from ..rules.fetcher import fetch_repo_rules_data
from ..rules.synthesizer import RulesDraftError, draft_rules
from . import get_config, main


logger = logging.getLogger(__name__)


def _mining_db_path(config: Config) -> Path:
    """Cache DB sits next to the state DB, e.g. ~/.bb_review/rules_mining.db."""
    return config.database.resolved_path.parent / "rules_mining.db"


def _guides_dir() -> Path:
    """Path to the repo's guides/ directory."""
    return Path(__file__).parent.parent.parent / "guides"


@main.group()
def rules() -> None:
    """Mine reviewer comments and draft repo review rules."""


@rules.command("fetch")
@click.argument("repo_name")
@click.option("--count", default=30, help="Max recent review requests to mine.")
@click.option("--days", default=0, help="Only mine RRs updated within N days (0 = no limit).")
@click.option("--refresh", is_flag=True, help="Re-fetch RRs even if already cached.")
@click.pass_context
def rules_fetch(ctx: click.Context, repo_name: str, count: int, days: int, refresh: bool) -> None:
    """Fetch reviewer comments for REPO_NAME into the mining cache."""
    config = get_config(ctx)
    repo_manager = RepoManager(config.get_all_repos())
    try:
        repo_config = repo_manager.get_repo(repo_name)
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

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

    mining_db = MiningDatabase(_mining_db_path(config))

    def _progress(current: int, total: int, n_comments: int) -> None:
        click.echo(f"\r  [{current}/{total}] processed (+{n_comments} comments)", nl=False)

    click.echo(f"Fetching reviewer comments for '{repo_name}' (last {count} RRs)...")
    counts = fetch_repo_rules_data(
        rb_client=rb_client,
        mining_db=mining_db,
        repo_name=repo_name,
        rb_repo_name=repo_config.rb_repo_name,
        bot_username=config.reviewboard.bot_username,
        count=count,
        days=days,
        refresh=refresh,
        on_progress=_progress,
    )
    click.echo()
    click.echo(
        f"Done: {counts['total']} RRs found, "
        f"{counts['fetched']} fetched, "
        f"{counts['skipped']} skipped, "
        f"{counts['comments']} comments cached."
    )


@rules.command("draft")
@click.argument("repo_name")
@click.option(
    "--method",
    type=click.Choice(["claude", "codex"]),
    default="claude",
    help="Agent backend for synthesis.",
)
@click.option(
    "--model",
    default=None,
    help="Agent model override (e.g. 'opus', 'sonnet'). Defaults to the agent's own default.",
)
@click.option(
    "--transcript",
    type=click.Path(path_type=Path),
    help="Save the agent transcript to this path.",
)
@click.pass_context
def rules_draft(
    ctx: click.Context,
    repo_name: str,
    method: str,
    model: str | None,
    transcript: Path | None,
) -> None:
    """Draft guides/REPO_NAME/draft-rules.md from cached comments."""
    config = get_config(ctx)
    repo_manager = RepoManager(config.get_all_repos())
    mining_db = MiningDatabase(_mining_db_path(config))

    click.echo(f"Drafting rules for '{repo_name}' via {method}...")
    try:
        out_path = draft_rules(
            repo_name=repo_name,
            mining_db=mining_db,
            repo_manager=repo_manager,
            guides_dir=_guides_dir(),
            method=method,
            model=model,
            transcript_path=transcript,
        )
    except (RulesDraftError, RepoManagerError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"Wrote {out_path}")


@rules.command("show")
@click.argument("repo_name")
@click.pass_context
def rules_show(ctx: click.Context, repo_name: str) -> None:
    """Show what is cached for REPO_NAME."""
    config = get_config(ctx)
    mining_db = MiningDatabase(_mining_db_path(config))
    stats = mining_db.get_repo_stats(repo_name)
    click.echo(f"Cached for '{repo_name}':")
    click.echo(f"  Review requests: {stats.review_request_count}")
    click.echo(f"  Comments:        {stats.comment_count}")
