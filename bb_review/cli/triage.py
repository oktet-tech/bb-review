"""Triage command for BB Review CLI.

Fetches review comments from RB, classifies them via LLM,
lets the user pick actions in a TUI, and outputs a fix plan.
"""

import logging
from pathlib import Path
import sys

import click

from ..config import Config
from ..git import RepoManager
from ..guidelines import load_guidelines
from ..reviewers.llm import extract_changed_files
from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher
from ..triage.analyzer import TriageAnalyzer
from ..triage.models import (
    FixPlan,
    FixPlanItem,
    SelectableTriagedComment,
    TriageResult,
)
from ..triage.plan_writer import write_fix_plan
from ..triage.replier import RBReplier
from . import get_config, main
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


@main.command()
@click.argument("review_id", type=REVIEW_ID)
@click.option(
    "--mode",
    type=click.Choice(["plan", "reply", "agent"]),
    default="plan",
    help="Execution mode: plan (write YAML), reply (plan + post replies), agent (plan + print cmd)",
)
@click.option("--dry-run", is_flag=True, help="Fetch and triage but do not write files or post")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output plan file path")
@click.option("-O", "--auto-output", is_flag=True, help="Auto-generate output: triage_{rr_id}.yaml")
@click.option("--no-tui", is_flag=True, help="Skip interactive TUI, use LLM-suggested defaults")
@click.option("--diff-revision", type=int, default=None, help="Specific diff revision to use")
@click.pass_context
def triage(
    ctx: click.Context,
    review_id: int,
    mode: str,
    dry_run: bool,
    output: Path | None,
    auto_output: bool,
    no_tui: bool,
    diff_revision: int | None,
) -> None:
    """Triage review comments and plan fixes.

    Fetches all comments on REVIEW_ID, classifies them via LLM,
    then lets you pick actions (fix/reply/skip) in a TUI.

    REVIEW_ID can be a number or a Review Board URL.

    Examples:
        bb-review triage 42763 --dry-run --no-tui
        bb-review triage 42763 --no-tui -O
        bb-review triage 42763 --mode reply --dry-run
    """
    if output and auto_output:
        raise click.UsageError("Cannot use both -o and -O options")

    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    click.echo(f"Triaging comments on review request #{review_id}...")

    try:
        # Connect to RB
        rb_client = ReviewBoardClient(
            url=config.reviewboard.url,
            bot_username=config.reviewboard.bot_username,
            api_token=config.reviewboard.api_token,
            username=config.reviewboard.username,
            password=config.reviewboard.get_password(),
            use_kerberos=config.reviewboard.use_kerberos,
        )
        rb_client.connect()

        # Fetch comments
        fetcher = RBCommentFetcher(rb_client, config.reviewboard.bot_username)
        comments = fetcher.fetch_all_comments(review_id)

        if not comments:
            click.echo("No comments found on this review request.")
            return

        click.echo(f"  Found {len(comments)} comments")

        # Get diff and repo info
        diff_info = rb_client.get_diff(review_id, diff_revision)
        repo_info = rb_client.get_repository_info(review_id)
        repo_name = repo_info.get("name", "unknown")

        click.echo(f"  Repository: {repo_name}")
        click.echo(f"  Diff revision: {diff_info.diff_revision}")

        # Get file contexts (if we have the repo configured)
        repo_manager = RepoManager(config.get_all_repos())
        file_contexts = _get_file_contexts(
            repo_manager,
            repo_name,
            diff_info,
            config,
        )

        # Load guidelines for context
        guidelines_text = ""
        repo_config = repo_manager.get_repo_by_rb_name(repo_name)
        if repo_config:
            try:
                guidelines = load_guidelines(repo_config.local_path)
                if guidelines.context:
                    guidelines_text = guidelines.context
            except Exception:
                pass

        # Run LLM triage
        if dry_run:
            click.echo("\n[DRY RUN] Would triage the following comments:")
            for c in comments:
                loc = f"{c.file_path}:{c.line_number}" if c.file_path else "body"
                click.echo(f"  [{c.reviewer}] {loc}: {c.text[:80]}")
            click.echo(f"\n[DRY RUN] {len(comments)} comments would be classified")
            return

        click.echo("  Running LLM triage...")
        analyzer = TriageAnalyzer.from_config(
            provider_name=config.llm.provider,
            api_key=config.llm.api_key,
            model=config.llm.model,
            max_tokens=config.llm.max_tokens,
            base_url=config.llm.base_url,
            site_url=config.llm.site_url,
            site_name=config.llm.site_name or "BB Review",
        )
        triage_result = analyzer.analyze(
            comments=comments,
            diff=diff_info.raw_diff,
            file_contexts=file_contexts,
            guidelines_text=guidelines_text,
        )
        triage_result.review_request_id = review_id

        click.echo(f"  Triage complete: {triage_result.summary}")
        _print_triage_summary(triage_result)

        # Interactive or auto mode
        if no_tui:
            selectables = [SelectableTriagedComment.from_triaged(t) for t in triage_result.triaged_comments]
        else:
            selectables = _run_triage_tui(triage_result, mode)
            if selectables is None:
                click.echo("Triage cancelled.")
                return

        # Build fix plan
        plan = _build_fix_plan(review_id, repo_name, selectables)

        # Execute based on mode
        output_path = output
        if auto_output:
            output_path = Path(f"triage_{review_id}.yaml")

        if output_path:
            write_fix_plan(plan, output_path)
            click.echo(f"\nFix plan written to: {output_path}")

        if mode == "reply":
            _execute_reply_mode(rb_client, review_id, plan, comments, dry_run=False)
        elif mode == "agent":
            _print_agent_command(review_id, output_path, plan)

        # Print summary
        click.echo(f"\nPlan: {plan.fix_count} fixes, {plan.reply_count} replies, {plan.skip_count} skipped")

    except Exception as e:
        logger.exception("Triage failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _get_file_contexts(
    repo_manager: RepoManager,
    repo_name: str,
    diff_info,
    config: Config,
) -> dict[str, str]:
    """Try to get file contexts from the local repo."""
    repo_config = repo_manager.get_repo_by_rb_name(repo_name)
    if repo_config is None:
        return {}

    file_contexts: dict[str, str] = {}
    changed_files = extract_changed_files(diff_info.raw_diff)
    for file_info in changed_files[:10]:
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

    return file_contexts


def _print_triage_summary(result: TriageResult) -> None:
    """Print a brief summary of triage results."""
    from collections import Counter

    counts = Counter(t.classification.value for t in result.triaged_comments)
    parts = [f"{v} {k}" for k, v in counts.most_common()]
    click.echo(f"  Classifications: {', '.join(parts)}")


def _run_triage_tui(triage_result: TriageResult, mode: str) -> list[SelectableTriagedComment] | None:
    """Launch the interactive triage TUI screen."""
    from ..ui.screens.triage_screen import TriageApp

    selectables = [SelectableTriagedComment.from_triaged(t) for t in triage_result.triaged_comments]
    app = TriageApp(selectables=selectables, default_mode=mode)
    result = app.run()
    if result is None:
        return None
    return result


def _build_fix_plan(
    rr_id: int,
    repo_name: str,
    selectables: list[SelectableTriagedComment],
) -> FixPlan:
    """Convert selectable triage items to a fix plan."""
    items = []
    for s in selectables:
        items.append(
            FixPlanItem(
                comment_id=s.triaged.source.comment_id,
                action=s.action,
                file_path=s.triaged.source.file_path,
                line_number=s.triaged.source.line_number,
                classification=s.triaged.classification,
                difficulty=s.triaged.difficulty,
                reviewer=s.triaged.source.reviewer,
                original_text=s.triaged.source.text,
                fix_hint=s.triaged.fix_hint,
                reply_text=s.edited_reply,
            )
        )
    return FixPlan(review_request_id=rr_id, repository=repo_name, items=items)


def _execute_reply_mode(
    rb_client: ReviewBoardClient,
    rr_id: int,
    plan: FixPlan,
    comments,
    dry_run: bool,
) -> None:
    """Post replies to RB for items with reply text."""
    # Build comment_id -> review_id mapping
    review_comment_map = {c.comment_id: c.review_id for c in comments}

    replier = RBReplier(rb_client)
    published = replier.post_replies(rr_id, plan.items, review_comment_map, dry_run=dry_run)

    if published:
        click.echo(f"  Posted {len(published)} replies to Review Board")
    elif not dry_run:
        click.echo("  No replies to post (no items with reply text)")


def _print_agent_command(
    rr_id: int,
    output_path: Path | None,
    plan: FixPlan,
) -> None:
    """Print suggested agent command for implementing fixes."""
    plan_ref = str(output_path) if output_path else f"triage_{rr_id}.yaml"
    click.echo("\nSuggested command to implement fixes:")
    click.echo(f'  claude --print "Implement fixes from {plan_ref}"')
    click.echo(f"\n  Fix plan has {plan.fix_count} items to fix.")
