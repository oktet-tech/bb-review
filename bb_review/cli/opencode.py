"""OpenCode command for BB Review CLI."""

import logging
from pathlib import Path
import sys

import click

from ..guidelines import load_guidelines, validate_guidelines
from ..reviewers import (
    OpenCodeError,
    OpenCodeTimeoutError,
    build_review_prompt,
    check_opencode_available,
    extract_changed_files,
    filter_diff_by_paths,
    run_opencode_agent,
    run_opencode_review,
)
from . import get_config, main
from ._review_runner import (
    run_review_command,
)
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


@main.command("opencode")
@click.argument("review_id", type=REVIEW_ID)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Do git ops but skip OpenCode calls, show what would be analyzed",
)
@click.option(
    "--fake-review",
    is_flag=True,
    help="Do everything but use mock OpenCode responses (for testing)",
)
@click.option("--model", "-m", help="Override opencode model")
@click.option("--timeout", default=300, type=int, help="Timeout in seconds for opencode")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw response to file")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output JSON file")
@click.option("-O", "--auto-output", is_flag=True, help="Auto-generate output: review_{id}.json")
@click.option(
    "--fallback",
    is_flag=True,
    help="If patch doesn't apply cleanly, pass patch file to OpenCode",
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
@click.pass_context
def opencode_cmd(
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
) -> None:
    """Analyze a review using OpenCode agent.

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

    binary_path = config.opencode.binary_path
    if not dry_run and not fake_review:
        available, msg = check_opencode_available(binary_path)
        if not available:
            click.echo(f"Error: {msg}", err=True)
            sys.exit(1)
        logger.debug(msg)

    if model is None:
        model = config.opencode.model
    if timeout == 300:
        timeout = config.opencode.timeout

    click.echo(f"Analyzing review request #{review_id} with OpenCode...")

    def reviewer_fn(
        rr_id: int,
        summary: str,
        raw_diff: str,
        repo_path: Path,
        repo_config,
        at_reviewed_state: bool,
    ) -> str:
        return run_opencode_for_review(
            rr_id,
            summary,
            raw_diff,
            repo_path,
            repo_config,
            model,
            timeout,
            binary_path,
            at_reviewed_state,
        )

    run_review_command(
        config=config,
        review_id=review_id,
        reviewer_fn=reviewer_fn,
        method_label="OpenCode",
        model=model,
        timeout=timeout,
        dry_run=dry_run,
        fake_review=fake_review,
        dump_response=dump_response,
        output=output,
        auto_output=auto_output,
        fallback=fallback,
        chain=chain,
        chain_file=chain_file,
        base_commit=base_commit,
        keep_branch=keep_branch,
        review_from=review_from,
        default_model=config.opencode.model,
        analysis_method="opencode",
    )


def run_opencode_for_review(
    review_id: int,
    summary: str,
    raw_diff: str,
    repo_path: Path,
    repo_config,
    model: str | None,
    timeout: int,
    binary_path: str | None,
    at_reviewed_state: bool = True,
) -> str:
    """Run OpenCode analysis for a single review."""
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
    )

    click.echo(f"    Running OpenCode analysis ({len(raw_diff)} chars diff)...")

    try:
        return run_opencode_review(
            repo_path=repo_path,
            patch_content=raw_diff,
            prompt=prompt,
            review_id=review_id,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            at_reviewed_state=at_reviewed_state,
        )
    except OpenCodeTimeoutError as e:
        raise click.ClickException(f"OpenCode timed out after {timeout}s") from e
    except OpenCodeError as e:
        raise click.ClickException(str(e)) from e


def _run_api_review(
    review_id: int,
    summary: str,
    raw_diff: str,
    repo_path: Path,
    used_target: bool,
    model: str | None,
    timeout: int,
    binary_path: str | None,
) -> str | None:
    """Run API review for te-test-suite repos."""
    context_path = repo_path / ".bb_review_context.tmp"
    context_path.write_text(f"Review #{review_id}\n\nSummary:\n{summary}")

    try:
        if used_target:
            api_prompt = (
                "Review the staged changes (use `git diff --cached` to see them) "
                "with context @.bb_review_context.tmp"
            )
        else:
            patch_path = repo_path / ".bb_review_patch.tmp"
            patch_path.write_text(raw_diff)
            api_prompt = "Review the patch @.bb_review_patch.tmp with context @.bb_review_context.tmp"

        return run_opencode_agent(
            repo_path=repo_path,
            agent="api-reviewer",
            prompt=api_prompt,
            review_id=review_id,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
        )
    except OpenCodeTimeoutError:
        click.echo(f"  Warning: API review timed out after {timeout}s", err=True)
        return None
    except OpenCodeError as e:
        click.echo(f"  Warning: API review failed: {e}", err=True)
        return None
    finally:
        for tmp_file in [context_path, repo_path / ".bb_review_patch.tmp"]:
            try:
                tmp_file.unlink()
            except Exception:
                pass
