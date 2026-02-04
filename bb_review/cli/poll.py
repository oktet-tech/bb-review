"""Poll commands for BB Review CLI."""

import logging
import signal
import sys

import click

from ..git import RepoManager
from ..models import PendingReview
from ..poller import Poller, StateDatabase
from ..reviewers import Analyzer
from ..rr import Commenter, ReviewBoardClient
from . import get_config, main
from .analyze import process_review


logger = logging.getLogger(__name__)


@main.group()
def poll():
    """Polling commands for automated review."""
    pass


@poll.command("once")
@click.pass_context
def poll_once(ctx: click.Context) -> None:
    """Run a single poll cycle."""
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    click.echo("Running single poll cycle...")

    try:
        # Initialize components
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            bot_username=config.reviewboard.bot_username,
            api_token=config.reviewboard.api_token,
            username=config.reviewboard.username,
            password=config.reviewboard.get_password(),
            use_kerberos=config.reviewboard.use_kerberos,
        )
        rb_client.connect()

        repo_manager = RepoManager(config.get_all_repos())

        analyzer = Analyzer(
            api_key=config.llm.api_key,
            model=config.llm.model,
            provider=config.llm.provider,
            base_url=config.llm.base_url,
            site_url=config.llm.site_url,
            site_name=config.llm.site_name or "BB Review",
        )

        commenter = Commenter(
            rb_client=rb_client,
            auto_ship_it=config.defaults.auto_ship_it,
        )

        state_db = StateDatabase(config.database.resolved_path)

        poller = Poller(
            state_db=state_db,
            interval_seconds=config.polling.interval_seconds,
            max_reviews_per_cycle=config.polling.max_reviews_per_cycle,
        )

        def fetch_pending():
            return rb_client.get_pending_reviews(limit=50)

        def process_func(pending: PendingReview):
            result = process_review(
                review_id=pending.review_request_id,
                rb_client=rb_client,
                repo_manager=repo_manager,
                analyzer=analyzer,
                config=config,
                pending=pending,
            )
            commenter.post_review(result)
            return result

        processed = poller.run_once(fetch_pending, process_func)
        click.echo(f"Processed {processed} reviews")

    except Exception as e:
        logger.exception("Poll cycle failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@poll.command("daemon")
@click.pass_context
def poll_daemon(ctx: click.Context) -> None:
    """Run as a polling daemon."""
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    click.echo(f"Starting polling daemon (interval: {config.polling.interval_seconds}s)")
    click.echo("Press Ctrl+C to stop")

    try:
        # Initialize components
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            bot_username=config.reviewboard.bot_username,
            api_token=config.reviewboard.api_token,
            username=config.reviewboard.username,
            password=config.reviewboard.get_password(),
            use_kerberos=config.reviewboard.use_kerberos,
        )
        rb_client.connect()

        repo_manager = RepoManager(config.get_all_repos())

        analyzer = Analyzer(
            api_key=config.llm.api_key,
            model=config.llm.model,
            provider=config.llm.provider,
            base_url=config.llm.base_url,
            site_url=config.llm.site_url,
            site_name=config.llm.site_name or "BB Review",
        )

        commenter = Commenter(
            rb_client=rb_client,
            auto_ship_it=config.defaults.auto_ship_it,
        )

        state_db = StateDatabase(config.database.resolved_path)

        poller = Poller(
            state_db=state_db,
            interval_seconds=config.polling.interval_seconds,
            max_reviews_per_cycle=config.polling.max_reviews_per_cycle,
        )

        # Handle signals
        def signal_handler(signum, frame):
            click.echo("\nReceived shutdown signal...")
            poller.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        def fetch_pending():
            return rb_client.get_pending_reviews(limit=50)

        def process_func(pending: PendingReview):
            result = process_review(
                review_id=pending.review_request_id,
                rb_client=rb_client,
                repo_manager=repo_manager,
                analyzer=analyzer,
                config=config,
                pending=pending,
            )
            commenter.post_review(result)
            return result

        poller.run_daemon(fetch_pending, process_func)

    except Exception as e:
        logger.exception("Daemon failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@poll.command("status")
@click.pass_context
def poll_status(ctx: click.Context) -> None:
    """Show polling status and statistics."""
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    state_db = StateDatabase(config.database.resolved_path)

    poll_state = state_db.get_poll_state()
    stats = state_db.get_stats()

    click.echo("Polling Status")
    click.echo("=" * 40)
    click.echo(f"Last poll: {poll_state['last_poll_at'] or 'Never'}")
    click.echo(f"Reviews in last poll: {poll_state['last_poll_count']}")
    click.echo()
    click.echo("Statistics")
    click.echo("-" * 40)
    click.echo(f"Total processed: {stats['total_processed']}")
    click.echo(f"Successful: {stats['successful']}")
    click.echo(f"Failed: {stats['failed']}")
    click.echo(f"Total comments: {stats['total_comments']}")

    if stats["recent"]:
        click.echo()
        click.echo("Recent Reviews")
        click.echo("-" * 40)
        for r in stats["recent"][:5]:
            status = "✓" if r.get("success") else "✗"
            click.echo(
                f"  {status} #{r['review_request_id']} "
                f"(rev {r['diff_revision']}) - {r['comment_count']} comments"
            )
