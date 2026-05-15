"""Codex command for BB Review CLI."""

import logging
from pathlib import Path
import sys

import click

from ..guidelines import load_guidelines, validate_guidelines
from ..reviewers import (
    extract_changed_files,
    filter_diff_by_paths,
)
from ..reviewers.codex import (
    CodexError,
    CodexTimeoutError,
    build_review_prompt,
    build_series_review_prompt,
    check_codex_available,
    run_codex_review,
)
from . import get_config, main
from ._review_runner import run_review_command
from ._session import ReviewSession
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


@main.command("codex")
@click.argument("review_id", type=REVIEW_ID)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Do git ops but skip Codex calls, show what would be analyzed",
)
@click.option(
    "--fake-review",
    is_flag=True,
    help="Do everything but use mock responses (for testing)",
)
@click.option("--model", "-m", help="Override model (e.g. o3, gpt-4.1)")
@click.option("--timeout", default=300, type=int, help="Timeout in seconds")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw response to file")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output JSON file")
@click.option("-O", "--auto-output", is_flag=True, help="Auto-generate output: review_{id}.json")
@click.option(
    "--fallback",
    is_flag=True,
    help="If patch doesn't apply cleanly, pass patch file to Codex",
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
@click.option(
    "--sandbox",
    type=click.Choice(["read-only", "workspace-write"]),
    default=None,
    help="Codex sandbox mode (default: from config or read-only)",
)
@click.pass_context
def codex_cmd(
    ctx: click.Context,
    review_id: int,
    dry_run: bool,
    fake_review: bool,
    model: str | None,
    timeout: int,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    fallback: bool,
    chain: bool,
    chain_file: Path | None,
    base_commit: str | None,
    keep_branch: bool,
    review_from: int | None,
    verbose: bool,
    series: bool,
    sandbox: str | None,
) -> None:
    """Analyze a review using Codex CLI.

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

    cx_config = config.codex
    binary_path = cx_config.binary_path

    if not dry_run and not fake_review:
        available, msg = check_codex_available(binary_path)
        if not available:
            click.echo(f"Error: {msg}", err=True)
            sys.exit(1)
        logger.debug(msg)

    # Apply config defaults where CLI didn't override
    if model is None:
        model = cx_config.model
    if timeout == 300:
        timeout = cx_config.timeout
    if sandbox is None:
        sandbox = cx_config.sandbox or "read-only"

    click.echo(f"Analyzing review request #{review_id} with Codex...")

    def reviewer_fn(
        rr_id: int,
        summary: str,
        raw_diff: str,
        repo_path: Path,
        repo_config,
        at_reviewed_state: bool,
    ) -> str:
        return run_codex_for_review(
            rr_id,
            summary,
            raw_diff,
            repo_path,
            repo_config,
            model,
            timeout,
            binary_path,
            sandbox,
            at_reviewed_state,
            verbose=verbose,
        )

    def series_reviewer(reviews, base_ref, repo_path, repo_config) -> str:
        return run_codex_for_series(
            reviews,
            base_ref,
            repo_path,
            repo_config,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            sandbox=sandbox,
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
        method_label="Codex",
        analysis_method="codex",
        model=model,
        default_model=cx_config.model,
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


def run_codex_for_review(
    review_id: int,
    summary: str,
    raw_diff: str,
    repo_path: Path,
    repo_config,
    model: str | None,
    timeout: int,
    binary_path: str,
    sandbox: str,
    at_reviewed_state: bool = True,
    verbose: bool = False,
) -> str:
    """Run Codex analysis for a single review."""
    changed_file_infos = extract_changed_files(raw_diff)
    changed_files = [f["path"] for f in changed_file_infos]

    from ..guidelines_deploy import cleanup_deployed, deploy_agent_skills

    deploy_result = deploy_agent_skills(repo_path, repo_config.name, "codex")

    guidelines = load_guidelines(
        repo_path,
        repo_name=repo_config.name,
        changed_files=None if deploy_result.has_skill else changed_files,
        skip_rich_context=deploy_result.has_skill,
    )

    warnings = validate_guidelines(guidelines)
    for warning in warnings:
        click.echo(f"    Warning: {warning}", err=True)

    if guidelines.ignore_paths:
        raw_diff = filter_diff_by_paths(raw_diff, guidelines.ignore_paths)

    # Codex uses flat deploy -- convert to file list for prompt
    skill_files = (
        [str(p.relative_to(repo_path)) for p in deploy_result.deployed_files]
        if deploy_result.deployed_files
        else None
    )

    guidelines_context = ""
    if not skill_files:
        if guidelines.context:
            guidelines_context = guidelines.context
        if guidelines.custom_rules:
            if guidelines_context:
                guidelines_context += "\n\nCustom rules:\n"
            guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)

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
        skill_files=skill_files,
    )

    click.echo(f"    Running Codex analysis ({len(raw_diff)} chars diff)...")

    try:
        return run_codex_review(
            repo_path=repo_path,
            patch_content=raw_diff,
            prompt=prompt,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            sandbox=sandbox,
            at_reviewed_state=at_reviewed_state,
        )
    except CodexTimeoutError as e:
        raise click.ClickException(f"Codex timed out after {timeout}s") from e
    except CodexError as e:
        raise click.ClickException(str(e)) from e
    finally:
        cleanup_deployed(deploy_result)


def run_codex_for_series(
    reviews: list,
    base_ref: str,
    repo_path: Path,
    repo_config,
    model: str | None,
    timeout: int,
    binary_path: str,
    sandbox: str,
    verbose: bool = False,
) -> str:
    """Run Codex analysis for an entire patch series."""
    guidelines = load_guidelines(repo_path, repo_name=repo_config.name)

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

    click.echo("    Running Codex series analysis...")

    try:
        return run_codex_review(
            repo_path=repo_path,
            patch_content="",
            prompt=prompt,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            sandbox=sandbox,
            at_reviewed_state=True,
        )
    except CodexTimeoutError as e:
        raise click.ClickException(f"Codex timed out after {timeout}s") from e
    except CodexError as e:
        raise click.ClickException(str(e)) from e
