"""OpenCode command for BB Review CLI."""

from datetime import datetime
import json
import logging
from pathlib import Path
import sys

import click

from ..git import PatchApplyError, RepoManager
from ..guidelines import load_guidelines, validate_guidelines
from ..models import PendingReview
from ..reviewers import (
    OpenCodeError,
    OpenCodeTimeoutError,
    build_review_prompt,
    check_opencode_available,
    extract_changed_files,
    filter_diff_by_paths,
    parse_opencode_output,
    run_opencode_agent,
    run_opencode_review,
)
from ..rr import ReviewBoardClient, ReviewFormatter
from . import get_config, main
from .utils import REVIEW_ID


logger = logging.getLogger(__name__)


@main.command("opencode")
@click.argument("review_id", type=REVIEW_ID)
@click.option("--dry-run", is_flag=True, help="Don't post, just show what would be posted")
@click.option("--model", "-m", help="Override opencode model (e.g., anthropic/claude-sonnet-4-20250514)")
@click.option("--timeout", default=300, type=int, help="Timeout in seconds for opencode")
@click.option("--dump-response", type=click.Path(path_type=Path), help="Dump raw opencode response to file")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    help="Output JSON file for dry-run",
)
@click.option(
    "--auto-output",
    "-O",
    is_flag=True,
    help="Auto-generate output file: review_{id}.json",
)
@click.option(
    "--fallback",
    is_flag=True,
    help="If patch doesn't apply cleanly, fallback to passing patch file to OpenCode",
)
@click.pass_context
def opencode_cmd(
    ctx: click.Context,
    review_id: int,
    dry_run: bool,
    model: str | None,
    timeout: int,
    dump_response: Path | None,
    output: Path | None,
    auto_output: bool,
    fallback: bool,
) -> None:
    """Analyze a review using OpenCode agent.

    REVIEW_ID can be either a number (e.g., 42738) or a full Review Board URL
    (e.g., https://rb.example.com/r/42738/).

    This runs OpenCode within the repository directory, checking out the code
    to the reviewed state so OpenCode has full codebase context with accurate
    line numbers.

    By default, the command will fail if the patch cannot be applied cleanly
    to the local repository. Use --fallback to allow OpenCode to analyze the
    raw patch file instead (less accurate line numbers).

    Example:
        bb-review opencode 42738 --dry-run
        bb-review opencode https://rb.example.com/r/42738/ --fallback
    """
    # Validate output options
    if output and auto_output:
        raise click.UsageError("Cannot use both -o/--output and -O/--auto-output")

    # Resolve output file path
    if auto_output:
        output = Path(f"review_{review_id}.json")

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
        if diff_info.target_commit_id:
            click.echo(f"  Target commit: {diff_info.target_commit_id[:12]} (reviewing actual commit)")
        click.echo(f"  Base commit: {pending.base_commit or 'default branch'}")

        # Get raw diff for potential patch application
        raw_diff = diff_info.raw_diff

        # Process in checkout context (with patch applied if target commit unavailable)
        try:
            with repo_manager.checkout_context(
                repo_config.name,
                base_commit=pending.base_commit,
                branch=pending.branch,
                target_commit=diff_info.target_commit_id,
                patch=raw_diff,
                require_patch=not fallback,
            ) as (repo_path, used_target):
                if used_target:
                    click.echo("  Checked out to reviewed state")
                elif fallback and not used_target:
                    click.echo("  Using fallback: patch file will be passed to OpenCode")

                # Load guidelines
                guidelines = load_guidelines(repo_path)

                # Validate guidelines
                warnings = validate_guidelines(guidelines)
                for warning in warnings:
                    click.echo(f"  Warning: {warning}", err=True)

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

                # Extract changed files for the prompt
                changed_file_infos = extract_changed_files(raw_diff)
                changed_files = [f["path"] for f in changed_file_infos]

                # Build prompt
                focus_areas = [f.value for f in guidelines.focus]
                prompt = build_review_prompt(
                    repo_name=repo_config.name,
                    review_id=review_id,
                    summary=pending.summary,
                    guidelines_context=guidelines_context,
                    focus_areas=focus_areas,
                    at_reviewed_state=used_target,
                    changed_files=changed_files,
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
                        at_reviewed_state=used_target,
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

                    context_path = repo_path / ".bb_review_context.tmp"
                    context_path.write_text(f"Review #{review_id}\n\nSummary:\n{pending.summary}")

                    try:
                        if used_target:
                            # Changes are staged - use git diff --cached
                            api_prompt = (
                                "Review the staged changes (use `git diff --cached` to see them) "
                                "with context @.bb_review_context.tmp"
                            )
                        else:
                            # Fall back to patch file
                            patch_path = repo_path / ".bb_review_patch.tmp"
                            patch_path.write_text(raw_diff)
                            click.echo(f"  Patch file: {patch_path}")
                            api_prompt = (
                                "Review the patch @.bb_review_patch.tmp with context @.bb_review_context.tmp"
                            )

                        api_analysis = run_opencode_agent(
                            repo_path=repo_path,
                            agent="api-reviewer",
                            prompt=api_prompt,
                            review_id=review_id,
                            model=model,
                            timeout=timeout,
                            binary_path=binary_path,
                        )
                        click.echo("  API review completed")
                    except OpenCodeTimeoutError:
                        click.echo(f"  Warning: API review timed out after {timeout}s", err=True)
                    except OpenCodeError as e:
                        click.echo(f"  Warning: API review failed: {e}", err=True)
                    finally:
                        # Clean up temp files
                        for tmp_file in [context_path, repo_path / ".bb_review_patch.tmp"]:
                            try:
                                tmp_file.unlink()
                            except Exception:
                                pass

        except PatchApplyError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

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
            main_count = len(parsed.issues)
            api_count = len(api_parsed.issues)
            click.echo(f"  - {main_count} from main analysis, {api_count} from API review")
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

            rb_comments.append(
                {
                    "file_path": issue.file_path,
                    "line_number": issue.line_number,
                    "text": "\n".join(text_parts),
                }
            )

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

        # Helper to build submission data for saving
        def build_submission_data():
            unparsed_parts = []
            if parsed.unparsed_text:
                unparsed_parts.append(parsed.unparsed_text)
            if api_parsed and api_parsed.unparsed_text:
                unparsed_parts.append(f"--- API Review ---\n{api_parsed.unparsed_text}")
            combined_unparsed = "\n\n".join(unparsed_parts)

            parsed_issues = [
                {
                    "title": issue.title,
                    "file_path": issue.file_path,
                    "line_number": issue.line_number,
                    "severity": issue.severity,
                    "issue_type": issue.issue_type,
                    "comment": issue.comment,
                    "suggestion": issue.suggestion,
                }
                for issue in all_issues
            ]

            return ReviewFormatter.format_for_submission(
                review_request_id=review_id,
                body_top=body_top,
                comments=rb_comments,
                ship_it=False,
                unparsed_text=combined_unparsed,
                parsed_issues=parsed_issues,
                metadata={
                    "created_at": datetime.now().isoformat(),
                    "model": model or "default",
                    "dry_run": dry_run,
                },
            ), combined_unparsed

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

            # Save to file if -O was used
            if output:
                submission_data, _ = build_submission_data()
                output.write_text(json.dumps(submission_data, indent=2))
                click.echo(f"  - Saved to {output}")
        else:
            submission_data, combined_unparsed = build_submission_data()

            # Determine output file path (default to review_{id}.json in dry-run)
            output_file = output or Path(f"review_{review_id}.json")
            output_file.write_text(json.dumps(submission_data, indent=2))
            click.echo(f"\n[Dry run - review saved to {output_file}]")
            click.echo(f"  - {len(rb_comments)} inline comments")
            click.echo(f"  - {len(general_issues)} general issues in body")
            if combined_unparsed:
                click.echo(f"  - {len(combined_unparsed)} chars of unparsed text included")
            click.echo(f"\nTo submit: bb-review submit {output_file}")

    except Exception as e:
        logger.exception("Failed to analyze review with OpenCode")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
