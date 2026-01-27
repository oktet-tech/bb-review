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
from .models import PendingReview, ReviewGuidelines, ReviewResult
from .opencode_runner import (
    OpenCodeError,
    OpenCodeNotFoundError,
    OpenCodeTimeoutError,
    ParsedReview,
    build_review_prompt,
    check_opencode_available,
    parse_opencode_output,
    run_opencode_agent,
    run_opencode_review,
)
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
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose
    
    # Don't load config here - let commands that need it load it themselves
    # This allows commands like encrypt-password to work without a config
    setup_logging("DEBUG" if verbose else "INFO")
    ctx.obj["config"] = None


def get_config(ctx: click.Context) -> Config:
    """Load and return config, caching it in context."""
    if ctx.obj.get("config") is not None:
        return ctx.obj["config"]
    
    config_path = ctx.obj.get("config_path")
    verbose = ctx.obj.get("verbose", False)
    
    cfg = load_config(config_path)
    set_config(cfg)
    ensure_directories(cfg)
    
    log_level = "DEBUG" if verbose else cfg.logging.level
    setup_logging(log_level, cfg.logging.resolved_file)
    
    ctx.obj["config"] = cfg
    return cfg


@main.command()
@click.argument("review_id", type=int)
@click.option("--dry-run", is_flag=True, help="Don't post, just show what would be posted")
@click.option("--format", "output_format", type=click.Choice(["text", "json", "markdown"]), default="text")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw LLM response to file")
@click.pass_context
def analyze(ctx: click.Context, review_id: int, dry_run: bool, output_format: str, dump_response: Optional[Path]) -> None:
    """Analyze a specific review request."""
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    click.echo(f"Analyzing review request #{review_id}...")

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

        # Dump raw response if requested
        if dump_response:
            raw = analyzer.get_last_raw_response()
            if raw is not None:
                dump_response.write_text(raw if raw else "(empty response)")
                click.echo(f"Raw LLM response saved to: {dump_response}")
                if not raw:
                    click.echo("Warning: LLM returned empty response", err=True)
            else:
                click.echo("No raw response available (analysis may have failed)", err=True)

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
            submitter=rr.get("submitter", {}).get("username", "unknown"),
            summary=rr.get("summary", ""),
            diff_revision=diff_info.diff_revision,
            base_commit=diff_info.base_commit_id,
            branch=rr.get("branch"),
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


@main.command("opencode")
@click.argument("review_id", type=int)
@click.option("--dry-run", is_flag=True, help="Don't post, just show what would be posted")
@click.option("--model", "-m", help="Override opencode model (e.g., anthropic/claude-sonnet-4-20250514)")
@click.option("--timeout", default=300, type=int, help="Timeout in seconds for opencode")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw opencode response to file")
@click.pass_context
def opencode_cmd(
    ctx: click.Context,
    review_id: int,
    dry_run: bool,
    model: Optional[str],
    timeout: int,
    dump_response: Optional[Path],
) -> None:
    """Analyze a review using OpenCode agent.

    This runs OpenCode in Plan mode within the repository directory,
    giving it full codebase context for more thorough analysis.

    The patch is passed as a file attachment and OpenCode analyzes it
    without making any changes to the repository.

    Example:
        bb-review opencode 42738 --dry-run
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    # Check if opencode is available
    binary_path = config.opencode.binary_path
    available, msg = check_opencode_available(binary_path)
    if not available:
        click.echo(f"Error: {msg}", err=True)
        sys.exit(1)
    
    logger.debug(msg)

    # Use config values if not overridden
    if model is None:
        model = config.opencode.model
    if timeout == 300:  # default value
        timeout = config.opencode.timeout

    click.echo(f"Analyzing review request #{review_id} with OpenCode...")

    try:
        # Initialize RB client
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

        # Get review info
        rr = rb_client.get_review_request(review_id)
        repo_info = rb_client.get_repository_info(review_id)
        diff_info = rb_client.get_diff(review_id)

        pending = PendingReview(
            review_request_id=review_id,
            repository=repo_info["name"],
            submitter=rr.get("submitter", {}).get("username", "unknown"),
            summary=rr.get("summary", ""),
            diff_revision=diff_info.diff_revision,
            base_commit=diff_info.base_commit_id,
            branch=rr.get("branch"),
        )

        # Get repository config
        repo_config = repo_manager.get_repo_by_rb_name(pending.repository)
        if repo_config is None:
            raise click.ClickException(
                f"Repository not configured: {pending.repository}. "
                f"Add it to config.yaml under 'repositories'."
            )

        click.echo(f"  Repository: {repo_config.name}")
        click.echo(f"  Base commit: {pending.base_commit or 'default branch'}")

        # Process in checkout context
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
            raw_diff = diff_info.raw_diff

            # Filter ignored paths
            if guidelines.ignore_paths:
                raw_diff = filter_diff_by_paths(raw_diff, guidelines.ignore_paths)

            # Build guidelines context for prompt
            guidelines_context = ""
            if guidelines.context:
                guidelines_context = guidelines.context
            if guidelines.custom_rules:
                if guidelines_context:
                    guidelines_context += "\n\nCustom rules:\n"
                guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)

            # Build prompt
            focus_areas = [f.value for f in guidelines.focus]
            prompt = build_review_prompt(
                repo_name=repo_config.name,
                review_id=review_id,
                summary=pending.summary,
                guidelines_context=guidelines_context,
                focus_areas=focus_areas,
            )

            click.echo(f"  Running OpenCode analysis ({len(raw_diff)} chars diff)...")
            if model:
                click.echo(f"  Model: {model}")

            # Run opencode
            try:
                analysis = run_opencode_review(
                    repo_path=repo_path,
                    patch_content=raw_diff,
                    prompt=prompt,
                    review_id=review_id,
                    model=model,
                    timeout=timeout,
                    binary_path=binary_path,
                )
            except OpenCodeTimeoutError:
                click.echo(f"Error: OpenCode timed out after {timeout}s", err=True)
                sys.exit(1)
            except OpenCodeError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)

            # Run API review for te-test-suite repos
            api_analysis = None
            if repo_config.repo_type == "te-test-suite":
                click.echo("  Running API review via api-reviewer agent...")
                
                # Create temp patch file for the agent
                import tempfile
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".patch",
                    delete=False,
                    prefix="bb_review_api_",
                ) as patch_file:
                    patch_file.write(raw_diff)
                    patch_path = Path(patch_file.name)
                
                try:
                    api_analysis = run_opencode_agent(
                        repo_path=repo_path,
                        agent="api-reviewer",
                        prompt="Review the attached patch for correct API usage against OL Test Environment.",
                        review_id=review_id,
                        model=model,
                        timeout=timeout,
                        binary_path=binary_path,
                        patch_file=patch_path,
                    )
                except OpenCodeTimeoutError:
                    click.echo(f"  Warning: API review timed out after {timeout}s", err=True)
                except OpenCodeError as e:
                    click.echo(f"  Warning: API review failed: {e}", err=True)
                finally:
                    try:
                        patch_path.unlink()
                    except Exception:
                        pass

        # Dump raw response if requested
        if dump_response:
            dump_content = analysis
            if api_analysis:
                dump_content += "\n\n" + "=" * 60 + "\nAPI Review:\n" + "=" * 60 + "\n" + api_analysis
            dump_response.write_text(dump_content)
            click.echo(f"Raw OpenCode response saved to: {dump_response}")

        # Parse the analysis output
        parsed = parse_opencode_output(analysis)

        # Display the analysis
        click.echo("\n" + "=" * 60)
        click.echo("OpenCode Analysis:")
        click.echo("=" * 60)
        click.echo(analysis)
        click.echo("=" * 60)

        # Parse and display API review if available
        api_parsed = None
        if api_analysis:
            api_parsed = parse_opencode_output(api_analysis)
            click.echo("\n" + "=" * 60)
            click.echo("API Review (/review-api):")
            click.echo("=" * 60)
            click.echo(api_analysis)
            click.echo("=" * 60)

        # Merge API review issues if available
        all_issues = list(parsed.issues)
        if api_parsed:
            # Tag API issues so we can identify them
            for issue in api_parsed.issues:
                issue.title = f"[API] {issue.title}"
            all_issues.extend(api_parsed.issues)

        # Show parsing results
        inline_comments = []
        general_issues = []

        for issue in all_issues:
            if issue.file_path and issue.line_number:
                inline_comments.append(issue)
            else:
                general_issues.append(issue)

        click.echo(f"\nParsed {len(all_issues)} issues:")
        if api_parsed:
            click.echo(f"  - {len(parsed.issues)} from main analysis, {len(api_parsed.issues)} from API review")
        click.echo(f"  - {len(inline_comments)} with file:line (will be inline comments)")
        click.echo(f"  - {len(general_issues)} general (will be in body)")
        if parsed.unparsed_text:
            click.echo(f"  - Unparsed text: {len(parsed.unparsed_text)} chars")

        # Build comments for RB API
        rb_comments = []
        for issue in inline_comments:
            # Build comment text (markdown - enabled via text_type in rb_client)
            text_parts = [f"**{issue.title}**"]
            if issue.severity:
                text_parts.append(f"Severity: {issue.severity}")
            if issue.comment:
                text_parts.append(issue.comment)
            if issue.suggestion:
                text_parts.append(f"\n**Suggestion:** {issue.suggestion}")

            rb_comments.append({
                "file_path": issue.file_path,
                "line_number": issue.line_number,
                "text": "\n".join(text_parts),
            })

        # Build body_top from general issues + unparsed text
        body_parts = ["**AI Review (OpenCode)**\n"]

        if general_issues:
            body_parts.append("## General Issues\n")
            for issue in general_issues:
                body_parts.append(f"### {issue.title}")
                if issue.file_path:
                    body_parts.append(f"**File:** `{issue.file_path}`")
                if issue.severity:
                    body_parts.append(f"**Severity:** {issue.severity}")
                if issue.comment:
                    body_parts.append(issue.comment)
                if issue.suggestion:
                    body_parts.append(f"**Suggestion:** {issue.suggestion}")
                body_parts.append("")  # blank line

        if parsed.unparsed_text:
            body_parts.append("## Additional Notes\n")
            body_parts.append(parsed.unparsed_text)

        if parsed.summary:
            body_parts.append(f"\n## Summary\n{parsed.summary}")

        # Add API review content if available
        if api_parsed:
            if api_parsed.unparsed_text:
                body_parts.append("\n## API Review Notes\n")
                body_parts.append(api_parsed.unparsed_text)
            if api_parsed.summary:
                body_parts.append(f"\n## API Review Summary\n{api_parsed.summary}")

        body_top = "\n".join(body_parts)

        # Post to Review Board
        if not dry_run:
            review_posted = rb_client.post_review(
                review_request_id=review_id,
                body_top=body_top,
                comments=rb_comments,
                ship_it=False,
            )
            click.echo(f"\nPosted review (ID: {review_posted})")
            click.echo(f"  - {len(rb_comments)} inline comments")
        else:
            click.echo("\n[Dry run - review not posted]")
            if rb_comments:
                click.echo("\nInline comments that would be posted:")
                for c in rb_comments:
                    click.echo(f"  {c['file_path']}:{c['line_number']}")
                    # Show first 100 chars of text
                    preview = c['text'][:100] + "..." if len(c['text']) > 100 else c['text']
                    click.echo(f"    {preview}")

    except Exception as e:
        logger.exception("Failed to analyze review with OpenCode")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


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
    try:
        config = get_config(ctx)
    except FileNotFoundError:
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
    try:
        config = get_config(ctx)
    except FileNotFoundError:
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
    """Copy guidelines from guides/ folder to repository cache.
    
    Looks for guides/{repo_name}.ai-review.yaml and copies it to the
    repository's local path. Creates a generic template if no guide exists.
    """
    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    repo_manager = RepoManager(config.get_all_repos())
    
    try:
        repo_path = repo_manager.get_local_path(repo_name)
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    
    target_path = repo_path / ".ai-review.yaml"
    
    # Check if target already exists
    if target_path.exists() and not force:
        click.echo(
            f"Guidelines file already exists: {target_path}\n"
            "Use --force to overwrite.",
            err=True,
        )
        sys.exit(1)
    
    # Look for repo-specific guide in guides/ folder
    guides_dir = Path(__file__).parent.parent / "guides"
    guide_file = guides_dir / f"{repo_name}.ai-review.yaml"
    
    if guide_file.exists():
        # Copy from guides/
        import shutil
        shutil.copy(guide_file, target_path)
        click.echo(f"Copied guide from: {guide_file}")
        click.echo(f"Created: {target_path}")
    else:
        # Create generic template
        path = create_example_guidelines(repo_path, overwrite=force)
        click.echo(f"No guide found for '{repo_name}' in {guides_dir}")
        click.echo(f"Created generic template: {path}")


@repos.command("mcp-setup")
@click.argument("repo_name")
@click.option("--force", is_flag=True, help="Overwrite existing files")
@click.pass_context
def repos_mcp_setup(ctx: click.Context, repo_name: str, force: bool) -> None:
    """Setup OpenCode MCP configuration for te-test-suite repositories.
    
    For repositories with repo_type: te-test-suite, this command copies:
    - opencode/te-ts-reviewer -> {repo}/.opencode/agent/api-reviewer.md
    - opencode/te-review-command -> {repo}/.opencode/command/review-api.md
    - opencode/ts-te-mcp -> {repo}/opencode.json
    
    This enables OpenCode to use the ol-te-dev MCP server for API review.
    """
    import shutil

    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required", err=True)
        sys.exit(1)

    repo_manager = RepoManager(config.get_all_repos())
    
    # Get repo config to check type
    repo_config = repo_manager.get_repo(repo_name)
    
    if repo_config.repo_type != "te-test-suite":
        click.echo(
            f"Error: Repository '{repo_name}' has type '{repo_config.repo_type}', "
            f"expected 'te-test-suite'.\n"
            f"Set repo_type: te-test-suite in config.yaml to enable MCP setup.",
            err=True,
        )
        sys.exit(1)
    
    try:
        repo_path = repo_manager.get_local_path(repo_name)
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    
    if not repo_path.exists():
        click.echo(f"Error: Repository not cloned. Run 'bb-review repos sync {repo_name}' first.", err=True)
        sys.exit(1)
    
    # Source files from opencode/ directory
    opencode_src_dir = Path(__file__).parent.parent / "opencode"
    reviewer_src = opencode_src_dir / "te-ts-reviewer"
    command_src = opencode_src_dir / "te-review-command"
    mcp_config_src = opencode_src_dir / "ts-te-mcp"
    
    # Check source files exist
    if not reviewer_src.exists():
        click.echo(f"Error: Source file not found: {reviewer_src}", err=True)
        sys.exit(1)
    if not command_src.exists():
        click.echo(f"Error: Source file not found: {command_src}", err=True)
        sys.exit(1)
    if not mcp_config_src.exists():
        click.echo(f"Error: Source file not found: {mcp_config_src}", err=True)
        sys.exit(1)
    
    # Target paths
    reviewer_target = repo_path / ".opencode" / "agent" / "api-reviewer.md"
    command_target = repo_path / ".opencode" / "command" / "review-api.md"
    mcp_config_target = repo_path / "opencode.json"
    
    # Check if targets exist
    if reviewer_target.exists() and not force:
        click.echo(f"Error: {reviewer_target} already exists. Use --force to overwrite.", err=True)
        sys.exit(1)
    if command_target.exists() and not force:
        click.echo(f"Error: {command_target} already exists. Use --force to overwrite.", err=True)
        sys.exit(1)
    if mcp_config_target.exists() and not force:
        click.echo(f"Error: {mcp_config_target} already exists. Use --force to overwrite.", err=True)
        sys.exit(1)
    
    # Create directories and copy files
    reviewer_target.parent.mkdir(parents=True, exist_ok=True)
    command_target.parent.mkdir(parents=True, exist_ok=True)
    
    shutil.copy(reviewer_src, reviewer_target)
    click.echo(f"Created: {reviewer_target}")
    
    shutil.copy(command_src, command_target)
    click.echo(f"Created: {command_target}")
    
    shutil.copy(mcp_config_src, mcp_config_target)
    click.echo(f"Created: {mcp_config_target}")
    
    click.echo(f"\nMCP setup complete for '{repo_name}'")
    click.echo("OpenCode can now use the ol-te-dev MCP server for API review.")


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


@main.command("encrypt-password")
@click.option("--token", "-t", help="Token to use as encryption key (defaults to api_token from config)")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output file path (defaults to password_file from config or ~/.bb_review/password.enc)")
@click.pass_context
def encrypt_password_cmd(ctx: click.Context, token: Optional[str], output: Optional[Path]) -> None:
    """Encrypt your Review Board password for secure storage.
    
    The password will be encrypted using the api_token from your config
    (or --token if specified). The encrypted file is saved to password_file
    from config (or --output if specified).
    
    Example:
    
        # Uses api_token and password_file from config.yaml
        bb-review encrypt-password
        
        # Or specify explicitly
        bb-review encrypt-password --token "your-token" --output ~/.bb_review/password.enc
    """
    from .crypto import encrypt_password_to_file
    
    # Try to get token from config if not provided
    if not token:
        try:
            config = get_config(ctx)
            token = config.reviewboard.api_token or config.reviewboard.encryption_token
            if not token:
                click.echo("Error: No --token provided and no api_token in config", err=True)
                sys.exit(1)
            click.echo(f"Using api_token from config ({token[:20]}...)")
        except (FileNotFoundError, Exception) as e:
            click.echo(f"Error: No --token provided and couldn't load config: {e}", err=True)
            sys.exit(1)
    
    # Try to get output path from config if not provided
    if not output:
        try:
            config = get_config(ctx)
            if config.reviewboard.password_file:
                output = Path(config.reviewboard.password_file)
                click.echo(f"Using password_file from config: {output}")
            else:
                output = Path("~/.bb_review/password.enc")
        except (FileNotFoundError, Exception):
            output = Path("~/.bb_review/password.enc")
    
    password = click.prompt("Enter your Review Board password", hide_input=True)
    confirm = click.prompt("Confirm password", hide_input=True)
    
    if password != confirm:
        click.echo("Passwords don't match!", err=True)
        sys.exit(1)
    
    output_path = output.expanduser()
    
    try:
        encrypt_password_to_file(password, token, output_path)
        click.echo(f"\nPassword encrypted and saved to: {output_path}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
