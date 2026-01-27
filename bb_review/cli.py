"""Command-line interface for BB Review."""

import json
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .analyzer import Analyzer, extract_changed_files, filter_diff_by_paths
from .commenter import Commenter, ReviewFormatter
from .config import Config, ensure_directories, load_config, set_config
from .guidelines import create_example_guidelines, load_guidelines, validate_guidelines
from .models import PendingReview, ReviewGuidelines
from .poller import Poller, StateDatabase
from .rb_client import ReviewBoardClient
from .repo_manager import RepoManager, RepoManagerError

logger = logging.getLogger(__name__)


def setup_logging(level: str, log_file: Optional[Path] = None) -> None:
    """Configure logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    handlers = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


@click.group()
@click.option(
    "--config", "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to config file",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.version_option(version=__version__)
@click.pass_context
def main(ctx: click.Context, config: Optional[Path], verbose: bool) -> None:
    """BB Review - AI-powered code review for Review Board."""
    ctx.ensure_object(dict)
    
    # Load config if available (some commands don't need it)
    try:
        cfg = load_config(config)
        set_config(cfg)
        ensure_directories(cfg)
        
        log_level = "DEBUG" if verbose else cfg.logging.level
        setup_logging(log_level, cfg.logging.resolved_file)
        
        ctx.obj["config"] = cfg
    except FileNotFoundError:
        # Config not required for all commands
        setup_logging("DEBUG" if verbose else "INFO")
        ctx.obj["config"] = None


@main.command()
@click.argument("review_id", type=int)
@click.option("--dry-run", is_flag=True, help="Don't post, just show what would be posted")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "markdown"]), default="text")
@click.pass_context
def analyze(ctx: click.Context, review_id: int, dry_run: bool, output_format: str) -> None:
    """Analyze a specific review request."""
    config: Config = ctx.obj.get("config")
    if not config:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    click.echo(f"Analyzing review request #{review_id}...")

    try:
        # Initialize components
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            api_token=config.reviewboard.api_token,
            bot_username=config.reviewboard.bot_username,
        )
        rb_client.connect()

        repo_manager = RepoManager(config.get_all_repos())
        
        analyzer = Analyzer(
            api_key=config.llm.api_key,
            model=config.llm.model,
            max_tokens=config.llm.max_tokens,
            temperature=config.llm.temperature,
            provider=config.llm.provider,
            base_url=config.llm.base_url,
            site_url=config.llm.site_url,
            site_name=config.llm.site_name or "BB Review",
        )

        commenter = Commenter(
            rb_client=rb_client,
            analyzer=analyzer,
            auto_ship_it=config.defaults.auto_ship_it,
        )

        # Process the review
        result = process_review(
            review_id=review_id,
            rb_client=rb_client,
            repo_manager=repo_manager,
            analyzer=analyzer,
            config=config,
        )

        # Output result
        if output_format == "json":
            click.echo(json.dumps(ReviewFormatter.format_as_json(result), indent=2))
        elif output_format == "markdown":
            click.echo(ReviewFormatter.format_as_markdown(result))
        else:
            click.echo(commenter.format_cli_output(result))

        # Post if not dry run
        if not dry_run:
            review_posted = commenter.post_review(result, dry_run=False)
            click.echo(f"\nPosted review (ID: {review_posted})")
        else:
            commenter.post_review(result, dry_run=True)

    except Exception as e:
        logger.exception("Failed to analyze review")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def process_review(
    review_id: int,
    rb_client: ReviewBoardClient,
    repo_manager: RepoManager,
    analyzer: Analyzer,
    config: Config,
    pending: Optional[PendingReview] = None,
):
    """Process a single review request."""
    # Get review info if not provided
    if pending is None:
        rr = rb_client.get_review_request(review_id)
        repo_info = rb_client.get_repository_info(review_id)
        diff_info = rb_client.get_diff(review_id)
        
        pending = PendingReview(
            review_request_id=review_id,
            repository=repo_info["name"],
            submitter="unknown",
            summary=rr.summary,
            diff_revision=diff_info.diff_revision,
            base_commit=diff_info.base_commit_id,
            branch=getattr(rr, "branch", None),
        )

    # Get repository config
    repo_config = repo_manager.get_repo_by_rb_name(pending.repository)
    if repo_config is None:
        raise click.ClickException(
            f"Repository not configured: {pending.repository}. "
            f"Add it to config.yaml under 'repositories'."
        )

    # Checkout the base commit
    click.echo(f"  Repository: {repo_config.name}")
    click.echo(f"  Base commit: {pending.base_commit or 'default branch'}")
    
    with repo_manager.checkout_context(
        repo_config.name,
        base_commit=pending.base_commit,
        branch=pending.branch,
    ) as repo_path:
        # Load guidelines
        guidelines = load_guidelines(repo_path)
        
        # Validate guidelines
        warnings = validate_guidelines(guidelines)
        for warning in warnings:
            click.echo(f"  Warning: {warning}", err=True)

        # Get the diff
        diff_info = rb_client.get_diff(review_id)
        diff = diff_info.raw_diff

        # Filter ignored paths
        if guidelines.ignore_paths:
            diff = filter_diff_by_paths(diff, guidelines.ignore_paths)

        # Get file context for changed files
        file_contexts = {}
        changed_files = extract_changed_files(diff)
        for file_info in changed_files[:10]:  # Limit context gathering
            file_path = file_info["path"]
            if file_info["lines"]:
                context = repo_manager.get_file_context(
                    repo_config.name,
                    file_path,
                    min(file_info["lines"]),
                    max(file_info["lines"]),
                    context_lines=30,
                )
                if context:
                    file_contexts[file_path] = context

        # Run analysis
        click.echo(f"  Analyzing diff ({len(diff)} chars)...")
        result = analyzer.analyze(
            diff=diff,
            guidelines=guidelines,
            file_contexts=file_contexts,
            review_request_id=review_id,
            diff_revision=diff_info.diff_revision,
        )

        click.echo(f"  Found {result.issue_count} issues")
        return result


@main.group()
def poll():
    """Polling commands for automated review."""
    pass


@poll.command("once")
@click.pass_context
def poll_once(ctx: click.Context) -> None:
    """Run a single poll cycle."""
    config: Config = ctx.obj.get("config")
    if not config:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    click.echo("Running single poll cycle...")

    try:
        # Initialize components
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            api_token=config.reviewboard.api_token,
            bot_username=config.reviewboard.bot_username,
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
            analyzer=analyzer,
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
    config: Config = ctx.obj.get("config")
    if not config:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    click.echo(f"Starting polling daemon (interval: {config.polling.interval_seconds}s)")
    click.echo("Press Ctrl+C to stop")

    try:
        # Initialize components
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            api_token=config.reviewboard.api_token,
            bot_username=config.reviewboard.bot_username,
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
            analyzer=analyzer,
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
    config: Config = ctx.obj.get("config")
    if not config:
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
    
    if stats['recent']:
        click.echo()
        click.echo("Recent Reviews")
        click.echo("-" * 40)
        for r in stats['recent'][:5]:
            status = "✓" if r.get("success") else "✗"
            click.echo(
                f"  {status} #{r['review_request_id']} "
                f"(rev {r['diff_revision']}) - {r['comment_count']} comments"
            )


@main.group()
def repos():
    """Repository management commands."""
    pass


@repos.command("list")
@click.pass_context
def repos_list(ctx: click.Context) -> None:
    """List configured repositories."""
    config: Config = ctx.obj.get("config")
    if not config:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    if not config.repositories:
        click.echo("No repositories configured")
        return

    repo_manager = RepoManager(config.get_all_repos())
    repos = repo_manager.list_repos()

    click.echo("Configured Repositories")
    click.echo("=" * 60)
    
    for repo in repos:
        status = "✓ cloned" if repo["exists"] else "✗ not cloned"
        click.echo(f"\n{repo['name']} ({status})")
        click.echo(f"  RB name: {repo['rb_name']}")
        click.echo(f"  Path: {repo['local_path']}")
        click.echo(f"  Remote: {repo['remote_url']}")
        if repo["exists"]:
            click.echo(f"  Branch: {repo.get('current_branch', 'unknown')}")
            click.echo(f"  Commit: {repo.get('current_commit', 'unknown')}")


@repos.command("sync")
@click.argument("repo_name", required=False)
@click.pass_context
def repos_sync(ctx: click.Context, repo_name: Optional[str]) -> None:
    """Fetch/sync repositories."""
    config: Config = ctx.obj.get("config")
    if not config:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    repo_manager = RepoManager(config.get_all_repos())

    if repo_name:
        click.echo(f"Syncing {repo_name}...")
        try:
            repo_manager.ensure_clone(repo_name)
            repo_manager.fetch_all(repo_name)
            click.echo(f"  ✓ {repo_name} synced")
        except RepoManagerError as e:
            click.echo(f"  ✗ {repo_name}: {e}", err=True)
            sys.exit(1)
    else:
        click.echo("Syncing all repositories...")
        results = repo_manager.fetch_all_repos()
        
        for name, success in results.items():
            status = "✓" if success else "✗"
            click.echo(f"  {status} {name}")


@repos.command("init-guidelines")
@click.argument("repo_name")
@click.option("--force", is_flag=True, help="Overwrite existing file")
@click.pass_context
def repos_init_guidelines(ctx: click.Context, repo_name: str, force: bool) -> None:
    """Create example .ai-review.yaml in a repository."""
    config: Config = ctx.obj.get("config")
    if not config:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    repo_manager = RepoManager(config.get_all_repos())
    
    try:
        repo_path = repo_manager.get_local_path(repo_name)
        path = create_example_guidelines(repo_path, overwrite=force)
        click.echo(f"Created: {path}")
    except FileExistsError:
        click.echo(
            "Guidelines file already exists. Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize BB Review configuration."""
    config_path = Path.cwd() / "config.yaml"
    example_path = Path(__file__).parent.parent / "config.example.yaml"
    
    if config_path.exists():
        click.echo(f"Config file already exists: {config_path}")
        if not click.confirm("Overwrite?"):
            return

    # Copy example config
    if example_path.exists():
        config_path.write_text(example_path.read_text())
    else:
        # Inline minimal config
        config_path.write_text("""\
# BB Review Configuration
reviewboard:
  url: "https://your-reviewboard-server.com"
  api_token: "${RB_API_TOKEN}"
  bot_username: "ai-reviewer"

llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "${ANTHROPIC_API_KEY}"

repositories: []

polling:
  interval_seconds: 300
  max_reviews_per_cycle: 10

database:
  path: "~/.bb_review/state.db"

defaults:
  focus:
    - bugs
    - security
  severity_threshold: "medium"
  auto_ship_it: false
""")

    click.echo(f"Created config file: {config_path}")
    click.echo("\nNext steps:")
    click.echo("1. Edit config.yaml with your Review Board URL and API token")
    click.echo("2. Add your repositories to the 'repositories' section")
    click.echo("3. Set ANTHROPIC_API_KEY environment variable")
    click.echo("4. Run 'bb-review repos sync' to clone repositories")


if __name__ == "__main__":
    main()
