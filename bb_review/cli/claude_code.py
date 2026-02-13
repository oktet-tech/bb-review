"""Claude Code command for BB Review CLI."""

import logging
from pathlib import Path
import sys

import click

from ..guidelines import load_guidelines, validate_guidelines
from ..reviewers import (
    ClaudeCodeError,
    ClaudeCodeTimeoutError,
    check_claude_available,
    extract_changed_files,
    filter_diff_by_paths,
)
from ..reviewers.claude_code import (
    build_review_prompt,
    build_series_review_prompt,
    run_claude_review,
)
from . import get_config, main
from ._review_runner import run_review_command
from ._session import ReviewSession
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


@main.command("claude")
@click.argument("review_id", type=REVIEW_ID)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Do git ops but skip Claude Code calls, show what would be analyzed",
)
@click.option(
    "--fake-review",
    is_flag=True,
    help="Do everything but use mock responses (for testing)",
)
@click.option("--model", "-m", help="Override Claude model (e.g. sonnet, opus)")
@click.option("--timeout", default=600, type=int, help="Timeout in seconds")
@click.option("--max-turns", default=30, type=int, help="Max agentic turns")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw response to file")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output JSON file")
@click.option("-O", "--auto-output", is_flag=True, help="Auto-generate output: review_{id}.json")
@click.option(
    "--fallback",
    is_flag=True,
    help="If patch doesn't apply cleanly, pass patch file to Claude",
)
@click.option(
    "--chain/--no-chain",
    default=True,
    help="Auto-resolve dependency chain (default: --chain)",
)
@click.option(
    "--chain-file",
    type=click.Path(exists=True, path_type=Path),
    help="Manual chain file (one RR ID per line)",
)
@click.option("--base-commit", help="Base commit SHA for chain (used with --chain-file)")
@click.option(
    "--keep-branch",
    is_flag=True,
    help="Don't delete the review branch after completion",
)
@click.option(
    "--mcp-config",
    type=click.Path(exists=True, path_type=Path),
    help="MCP servers config file (e.g. .mcp.json)",
)
@click.option(
    "--review-from",
    type=REVIEW_ID,
    help="Start reviewing from this RR (earlier patches applied as context only)",
)
@click.option("--verbose", "-V", is_flag=True, help="Detailed multi-paragraph explanations")
@click.option(
    "--series",
    is_flag=True,
    help="Review entire patch series as one unit (implies --chain)",
)
@click.pass_context
def claude_cmd(
    ctx: click.Context,
    review_id: int,
    dry_run: bool,
    fake_review: bool,
    model: str | None,
    timeout: int,
    max_turns: int,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    fallback: bool,
    chain: bool,
    chain_file: Path | None,
    base_commit: str | None,
    keep_branch: bool,
    mcp_config: Path | None,
    review_from: int | None,
    verbose: bool,
    series: bool,
) -> None:
    """Analyze a review using Claude Code CLI.

    REVIEW_ID can be either a number (e.g., 42738) or a full Review Board URL
    (e.g., https://rb.example.com/r/42738/).

    By default, reviews the entire dependency chain. Use --no-chain to review
    only the specified review request.

    Results are output to stdout by default. Use -O to auto-generate output
    files (review_{rr_id}.json for each review).
    """
    if output and auto_output:
        raise click.UsageError("Cannot use both -o/--output and -O/--auto-output")

    try:
        config = get_config(ctx)
    except FileNotFoundError:
        click.echo("Error: Config file required. Use --config or create config.yaml", err=True)
        sys.exit(1)

    cc_config = config.claude_code
    binary_path = cc_config.binary_path

    if not dry_run and not fake_review:
        available, msg = check_claude_available(binary_path)
        if not available:
            click.echo(f"Error: {msg}", err=True)
            sys.exit(1)
        logger.debug(msg)

    # Apply config defaults where CLI didn't override
    if model is None:
        model = cc_config.model
    if timeout == 600:
        timeout = cc_config.timeout
    if max_turns == 30:
        max_turns = cc_config.effective_max_turns(model)

    allowed_tools = cc_config.allowed_tools

    if mcp_config is None and cc_config.mcp_config:
        mcp_config = Path(cc_config.mcp_config)

    click.echo(f"Analyzing review request #{review_id} with Claude Code...")

    def reviewer_fn(
        rr_id: int,
        summary: str,
        raw_diff: str,
        repo_path: Path,
        repo_config,
        at_reviewed_state: bool,
    ) -> str:
        return run_claude_for_review(
            rr_id,
            summary,
            raw_diff,
            repo_path,
            repo_config,
            model,
            timeout,
            max_turns,
            binary_path,
            allowed_tools,
            at_reviewed_state,
            mcp_config,
            verbose=verbose,
        )

    def series_reviewer(reviews, base_ref, repo_path, repo_config) -> str:
        return run_claude_for_series(
            reviews,
            base_ref,
            repo_path,
            repo_config,
            model=model,
            timeout=timeout,
            max_turns=max_turns,
            binary_path=binary_path,
            allowed_tools=allowed_tools,
            mcp_config=mcp_config,
            verbose=verbose,
        )

    from ..git import RepoManager
    from ..rr import ReviewBoardClient

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

    session = ReviewSession(
        config=config,
        rb_client=rb_client,
        repo_manager=repo_manager,
        repo_config=None,
        method_label="Claude Code",
        analysis_method="claude_code",
        model=model,
        default_model=cc_config.model,
        fake_review=fake_review,
        reviewer_fn=reviewer_fn,
        series_reviewer_fn=series_reviewer if series else None,
    )

    run_review_command(
        session=session,
        review_id=review_id,
        timeout=timeout,
        dry_run=dry_run,
        dump_response=dump_response,
        output=output,
        auto_output=auto_output,
        fallback=fallback,
        chain=chain,
        chain_file=chain_file,
        base_commit=base_commit,
        keep_branch=keep_branch,
        review_from=review_from,
        series=series,
    )


def run_claude_for_review(
    review_id: int,
    summary: str,
    raw_diff: str,
    repo_path: Path,
    repo_config,
    model: str | None,
    timeout: int,
    max_turns: int,
    binary_path: str,
    allowed_tools: list[str] | None,
    at_reviewed_state: bool = True,
    mcp_config: Path | None = None,
    verbose: bool = False,
) -> str:
    """Run Claude Code analysis for a single review."""
    guidelines = load_guidelines(repo_path)

    warnings = validate_guidelines(guidelines)
    for warning in warnings:
        click.echo(f"    Warning: {warning}", err=True)

    if guidelines.ignore_paths:
        raw_diff = filter_diff_by_paths(raw_diff, guidelines.ignore_paths)

    guidelines_context = ""
    if guidelines.context:
        guidelines_context = guidelines.context
    if guidelines.custom_rules:
        if guidelines_context:
            guidelines_context += "\n\nCustom rules:\n"
        guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)

    changed_file_infos = extract_changed_files(raw_diff)
    changed_files = [f["path"] for f in changed_file_infos]

    focus_areas = [f.value for f in guidelines.focus]
    prompt = build_review_prompt(
        repo_name=repo_config.name,
        review_id=review_id,
        summary=summary,
        guidelines_context=guidelines_context,
        focus_areas=focus_areas,
        at_reviewed_state=at_reviewed_state,
        changed_files=changed_files,
        verbose=verbose,
    )

    click.echo(f"    Running Claude Code analysis ({len(raw_diff)} chars diff)...")

    try:
        return run_claude_review(
            repo_path=repo_path,
            patch_content=raw_diff,
            prompt=prompt,
            model=model,
            timeout=timeout,
            max_turns=max_turns,
            binary_path=binary_path,
            allowed_tools=allowed_tools,
            at_reviewed_state=at_reviewed_state,
            mcp_config=mcp_config,
        )
    except ClaudeCodeTimeoutError as e:
        raise click.ClickException(f"Claude Code timed out after {timeout}s") from e
    except ClaudeCodeError as e:
        raise click.ClickException(str(e)) from e


def run_claude_for_series(
    reviews: list,
    base_ref: str,
    repo_path: Path,
    repo_config,
    model: str | None,
    timeout: int,
    max_turns: int,
    binary_path: str,
    allowed_tools: list[str] | None,
    mcp_config: Path | None = None,
    verbose: bool = False,
) -> str:
    """Run Claude Code analysis for an entire patch series."""
    guidelines = load_guidelines(repo_path)

    warnings = validate_guidelines(guidelines)
    for warning in warnings:
        click.echo(f"    Warning: {warning}", err=True)

    guidelines_context = ""
    if guidelines.context:
        guidelines_context = guidelines.context
    if guidelines.custom_rules:
        if guidelines_context:
            guidelines_context += "\n\nCustom rules:\n"
        guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)

    focus_areas = [f.value for f in guidelines.focus]
    prompt = build_series_review_prompt(
        repo_name=repo_config.name,
        reviews=reviews,
        base_ref=base_ref,
        guidelines_context=guidelines_context,
        focus_areas=focus_areas,
        verbose=verbose,
    )

    click.echo("    Running Claude Code series analysis...")

    try:
        return run_claude_review(
            repo_path=repo_path,
            patch_content="",
            prompt=prompt,
            model=model,
            timeout=timeout,
            max_turns=max_turns,
            binary_path=binary_path,
            allowed_tools=allowed_tools,
            at_reviewed_state=True,
            mcp_config=mcp_config,
        )
    except ClaudeCodeTimeoutError as e:
        raise click.ClickException(f"Claude Code timed out after {timeout}s") from e
    except ClaudeCodeError as e:
        raise click.ClickException(str(e)) from e
