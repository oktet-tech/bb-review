"""Database commands for BB Review CLI."""

from datetime import datetime
import json
import logging
from pathlib import Path
import sys

import click

from ..db import (
    ReviewDatabase,
    export_chain_to_markdown,
    export_to_json,
    export_to_markdown,
)
from ..models import ReviewComment, ReviewFocus, ReviewResult, Severity
from . import get_config, main


logger = logging.getLogger(__name__)


def get_review_db(ctx: click.Context) -> ReviewDatabase:
    """Get the ReviewDatabase instance, ensuring it's enabled in config."""
    config = get_config(ctx)

    if not config.review_db.enabled:
        click.echo(
            "Error: Reviews database is not enabled.\n"
            "Add the following to your config.yaml:\n\n"
            "review_db:\n"
            "  enabled: true\n"
            "  path: ~/.bb_review/reviews.db",
            err=True,
        )
        sys.exit(1)

    return ReviewDatabase(config.review_db.resolved_path)


@main.group()
def db():
    """Reviews database commands."""
    pass


@db.command("list")
@click.option("--rr", "review_request_id", type=int, help="Filter by review request ID")
@click.option("--repo", "repository", help="Filter by repository name")
@click.option(
    "--status",
    type=click.Choice(["draft", "submitted", "obsolete", "invalid"]),
    help="Filter by status",
)
@click.option("--chain", "chain_id", help="Filter by chain ID")
@click.option("--limit", "-n", default=20, type=int, help="Maximum number of results")
@click.pass_context
def db_list(
    ctx: click.Context,
    review_request_id: int | None,
    repository: str | None,
    status: str | None,
    chain_id: str | None,
    limit: int,
) -> None:
    """List stored analyses.

    Examples:
        bb-review db list                    # List recent analyses
        bb-review db list --rr 42738         # List analyses for a specific RR
        bb-review db list --repo te-dev      # List analyses for a repo
        bb-review db list --status draft     # List draft analyses
    """
    review_db = get_review_db(ctx)

    analyses = review_db.list_analyses(
        review_request_id=review_request_id,
        repository=repository,
        status=status,
        chain_id=chain_id,
        limit=limit,
    )

    if not analyses:
        click.echo("No analyses found.")
        return

    click.echo(f"Found {len(analyses)} analyses:")
    click.echo("=" * 80)

    for a in analyses:
        status_icons = {"draft": "📝", "submitted": "✓", "obsolete": "⊘", "invalid": "✗"}
        status_icon = status_icons.get(a.status.value, "?")
        chain_info = f" [chain: {a.chain_id[:20]}...]" if a.chain_id else ""

        click.echo(
            f"\n{status_icon} ID {a.id}: RR #{a.review_request_id} (diff {a.diff_revision}){chain_info}"
        )
        click.echo(f"   Repo: {a.repository} | Method: {a.analysis_method.value} | Model: {a.model_used}")
        click.echo(f"   Issues: {a.issue_count} | Critical: {'Yes' if a.has_critical_issues else 'No'}")
        click.echo(f"   Analyzed: {a.analyzed_at.strftime('%Y-%m-%d %H:%M')}")
        if a.rr_summary:
            summary = a.rr_summary[:60] + "..." if len(a.rr_summary) > 60 else a.rr_summary
            click.echo(f"   Summary: {summary}")


@db.command("show")
@click.argument("analysis_id", type=int)
@click.option("--comments/--no-comments", default=True, help="Show comments")
@click.pass_context
def db_show(ctx: click.Context, analysis_id: int, comments: bool) -> None:
    """Show details of a specific analysis.

    Examples:
        bb-review db show 1                  # Show analysis ID 1
        bb-review db show 1 --no-comments    # Show without comments
    """
    review_db = get_review_db(ctx)

    analysis = review_db.get_analysis(analysis_id)
    if not analysis:
        click.echo(f"Error: Analysis {analysis_id} not found", err=True)
        sys.exit(1)

    click.echo(f"Analysis #{analysis.id}")
    click.echo("=" * 60)
    click.echo(f"Review Request: #{analysis.review_request_id}")
    click.echo(f"Diff Revision:  {analysis.diff_revision}")
    click.echo(f"Repository:     {analysis.repository}")
    click.echo(f"Status:         {analysis.status.value}")
    click.echo(f"Method:         {analysis.analysis_method.value}")
    click.echo(f"Model:          {analysis.model_used}")
    click.echo(f"Analyzed:       {analysis.analyzed_at.strftime('%Y-%m-%d %H:%M:%S')}")

    if analysis.base_commit_id:
        click.echo(f"Base Commit:    {analysis.base_commit_id[:12]}")
    if analysis.submitter:
        click.echo(f"Submitter:      {analysis.submitter}")
    if analysis.rr_summary:
        click.echo(f"RR Summary:     {analysis.rr_summary}")
    if analysis.chain_id:
        click.echo(f"Chain:          {analysis.chain_id} (position {analysis.chain_position})")
    if analysis.submitted_at:
        click.echo(f"Submitted:      {analysis.submitted_at.strftime('%Y-%m-%d %H:%M:%S')}")

    click.echo("")
    click.echo("Summary:")
    click.echo("-" * 40)
    click.echo(analysis.summary)
    click.echo("")

    click.echo(f"Issues: {analysis.issue_count}")
    click.echo(f"Critical Issues: {'Yes' if analysis.has_critical_issues else 'No'}")

    if comments and analysis.comments:
        click.echo("")
        click.echo("Comments:")
        click.echo("-" * 40)

        for i, c in enumerate(analysis.comments, 1):
            click.echo(f"\n[{i}] {c.file_path}:{c.line_number} ({c.severity} {c.issue_type})")
            click.echo(f"    {c.message}")
            if c.suggestion:
                click.echo(f"    Suggestion: {c.suggestion}")


@db.command("export")
@click.argument("analysis_id", type=int)
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file path")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "markdown"]),
    default="json",
    help="Output format (default: json)",
)
@click.pass_context
def db_export(ctx: click.Context, analysis_id: int, output: Path | None, output_format: str) -> None:
    """Export an analysis to JSON or Markdown.

    JSON format is compatible with 'bb-review submit'.

    Examples:
        bb-review db export 1                          # Export to stdout as JSON
        bb-review db export 1 -o review.json           # Export to file
        bb-review db export 1 --format markdown        # Export as Markdown
        bb-review db export 1 -o review.md --format markdown
    """
    review_db = get_review_db(ctx)

    analysis = review_db.get_analysis(analysis_id)
    if not analysis:
        click.echo(f"Error: Analysis {analysis_id} not found", err=True)
        sys.exit(1)

    if output_format == "json":
        data = export_to_json(analysis)
        content = json.dumps(data, indent=2)
    else:
        content = export_to_markdown(analysis)

    if output:
        output.write_text(content)
        click.echo(f"Exported analysis {analysis_id} to {output}")
    else:
        click.echo(content)


@db.command("stats")
@click.pass_context
def db_stats(ctx: click.Context) -> None:
    """Show database statistics.

    Examples:
        bb-review db stats
    """
    review_db = get_review_db(ctx)
    stats = review_db.get_stats()

    click.echo("Reviews Database Statistics")
    click.echo("=" * 40)
    click.echo(f"Total Analyses: {stats.total_analyses}")
    click.echo(f"Total Comments: {stats.total_comments}")
    click.echo(f"Total Chains:   {stats.total_chains}")

    if stats.by_status:
        click.echo("\nBy Status:")
        for status, count in sorted(stats.by_status.items()):
            click.echo(f"  {status}: {count}")

    if stats.by_repository:
        click.echo("\nBy Repository:")
        for repo, count in sorted(stats.by_repository.items()):
            click.echo(f"  {repo}: {count}")

    if stats.by_method:
        click.echo("\nBy Method:")
        for method, count in sorted(stats.by_method.items()):
            click.echo(f"  {method}: {count}")

    if stats.recent_analyses:
        click.echo("\nRecent Analyses:")
        for a in stats.recent_analyses[:5]:
            click.echo(f"  #{a.id}: RR {a.review_request_id} ({a.repository}) - {a.status.value}")


@db.command("mark")
@click.argument("analysis_id", type=int)
@click.option(
    "--status",
    type=click.Choice(["draft", "submitted", "obsolete", "invalid"]),
    required=True,
    help="New status",
)
@click.pass_context
def db_mark(ctx: click.Context, analysis_id: int, status: str) -> None:
    """Update the status of an analysis.

    Examples:
        bb-review db mark 1 --status submitted
        bb-review db mark 1 --status obsolete
    """
    review_db = get_review_db(ctx)

    analysis = review_db.get_analysis(analysis_id)
    if not analysis:
        click.echo(f"Error: Analysis {analysis_id} not found", err=True)
        sys.exit(1)

    old_status = analysis.status.value
    review_db.update_status(analysis_id, status)
    click.echo(f"Updated analysis {analysis_id}: {old_status} -> {status}")


@db.command("chain")
@click.argument("chain_id")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file path")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "markdown"]),
    default="text",
    help="Output format",
)
@click.pass_context
def db_chain(ctx: click.Context, chain_id: str, output: Path | None, output_format: str) -> None:
    """Show or export a chain of analyses.

    Examples:
        bb-review db chain 42762_20260130_120000
        bb-review db chain 42762_20260130_120000 --format markdown -o chain.md
    """
    review_db = get_review_db(ctx)

    chain = review_db.get_chain(chain_id)
    if not chain:
        click.echo(f"Error: Chain {chain_id} not found", err=True)
        sys.exit(1)

    if output_format == "markdown":
        content = export_chain_to_markdown(chain)
        if output:
            output.write_text(content)
            click.echo(f"Exported chain to {output}")
        else:
            click.echo(content)
    else:
        # Text format
        click.echo(f"Chain: {chain.chain_id}")
        click.echo("=" * 60)
        click.echo(f"Repository:    {chain.repository}")
        click.echo(f"Created:       {chain.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        click.echo(f"Reviews:       {chain.reviewed_count}")
        click.echo(f"Total Issues:  {chain.total_issues}")
        click.echo(f"Partial:       {'Yes' if chain.partial else 'No'}")
        if chain.failed_at_rr_id:
            click.echo(f"Failed at RR:  #{chain.failed_at_rr_id}")
        if chain.branch_name:
            click.echo(f"Branch:        {chain.branch_name}")

        click.echo("\nAnalyses:")
        for a in chain.analyses:
            click.echo(f"\n  [{a.chain_position}] RR #{a.review_request_id} (diff {a.diff_revision})")
            click.echo(f"      Issues: {a.issue_count} | Status: {a.status.value}")
            if a.rr_summary:
                summary = a.rr_summary[:50] + "..." if len(a.rr_summary) > 50 else a.rr_summary
                click.echo(f"      {summary}")


@db.command("cleanup")
@click.option("--older-than", "days", type=int, required=True, help="Remove analyses older than N days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without deleting")
@click.option("--force", "-f", is_flag=True, help="Don't ask for confirmation")
@click.pass_context
def db_cleanup(ctx: click.Context, days: int, dry_run: bool, force: bool) -> None:
    """Remove old analyses from the database.

    Examples:
        bb-review db cleanup --older-than 90           # Remove analyses older than 90 days
        bb-review db cleanup --older-than 30 --dry-run # Preview what would be deleted
    """
    review_db = get_review_db(ctx)

    # Get count of what would be deleted (we do this by checking)
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=days)

    # For dry run or confirmation, list what would be deleted
    analyses = review_db.list_analyses(limit=1000)
    to_delete = [a for a in analyses if a.analyzed_at < cutoff]

    if not to_delete:
        click.echo(f"No analyses older than {days} days found.")
        return

    click.echo(f"Found {len(to_delete)} analyses older than {days} days:")
    for a in to_delete[:10]:
        click.echo(f"  #{a.id}: RR {a.review_request_id} ({a.analyzed_at.strftime('%Y-%m-%d')})")
    if len(to_delete) > 10:
        click.echo(f"  ... and {len(to_delete) - 10} more")

    if dry_run:
        click.echo("\n[DRY RUN] No changes made.")
        return

    if not force:
        if not click.confirm(f"\nDelete {len(to_delete)} analyses?"):
            click.echo("Aborted.")
            return

    count = review_db.cleanup(days)
    click.echo(f"Removed {count} analyses.")


@db.command("search")
@click.argument("query")
@click.option("--limit", "-n", default=20, type=int, help="Maximum number of results")
@click.pass_context
def db_search(ctx: click.Context, query: str, limit: int) -> None:
    """Search analyses by RR ID or summary text.

    Examples:
        bb-review db search 42738              # Search by RR ID
        bb-review db search "memory leak"      # Search in summaries
    """
    review_db = get_review_db(ctx)

    # Try to parse as RR ID first
    try:
        rr_id = int(query)
        analyses = review_db.list_analyses(review_request_id=rr_id, limit=limit)
        if analyses:
            click.echo(f"Analyses for RR #{rr_id}:")
            for a in analyses:
                click.echo(
                    f"  #{a.id}: diff {a.diff_revision} | {a.status.value} | "
                    f"{a.issue_count} issues | {a.analyzed_at.strftime('%Y-%m-%d')}"
                )
            return
    except ValueError:
        pass

    # Search in summaries (basic text search)
    analyses = review_db.list_analyses(limit=1000)
    query_lower = query.lower()
    matches = [
        a
        for a in analyses
        if query_lower in a.summary.lower() or (a.rr_summary and query_lower in a.rr_summary.lower())
    ]

    if not matches:
        click.echo(f"No analyses found matching '{query}'")
        return

    click.echo(f"Found {len(matches)} analyses matching '{query}':")
    for a in matches[:limit]:
        click.echo(f"\n  #{a.id}: RR {a.review_request_id} ({a.repository})")
        click.echo(f"      {a.analyzed_at.strftime('%Y-%m-%d')} | {a.status.value} | {a.issue_count} issues")
        # Show matching context
        summary_preview = a.summary[:100] + "..." if len(a.summary) > 100 else a.summary
        click.echo(f"      {summary_preview}")


@db.command("import")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--repo", "repository", required=True, help="Repository name")
@click.option("--diff-rev", "diff_revision", type=int, default=1, help="Diff revision (default: 1)")
@click.option(
    "--method",
    type=click.Choice(["llm", "opencode"]),
    help="Analysis method (auto-detected if not specified)",
)
@click.option("--model", help="Model name (auto-detected from metadata if not specified)")
@click.pass_context
def db_import(
    ctx: click.Context,
    file: Path,
    repository: str,
    diff_revision: int,
    method: str | None,
    model: str | None,
) -> None:
    """Import a review JSON file into the database.

    The file should be a JSON file with review results, typically generated
    by 'bb-review analyze --dry-run' or 'bb-review opencode --dry-run'.

    Examples:
        bb-review db import result.json --repo te-dev
        bb-review db import review_42738.json --repo te-dev --diff-rev 2
    """
    review_db = get_review_db(ctx)

    # Read and parse the JSON file
    try:
        with open(file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        click.echo(f"Error: Invalid JSON file: {e}", err=True)
        sys.exit(1)

    # Extract required fields
    review_request_id = data.get("review_request_id")
    if not review_request_id:
        click.echo("Error: File missing 'review_request_id' field", err=True)
        sys.exit(1)

    # Get parsed issues (structured comment data)
    parsed_issues = data.get("parsed_issues", [])
    if not parsed_issues:
        # Fall back to comments if parsed_issues not available
        # Note: comments have less structured data
        click.echo("Warning: No 'parsed_issues' found, using 'comments' (limited data)", err=True)
        parsed_issues = []
        for c in data.get("comments", []):
            # Try to parse severity from text if available
            parsed_issues.append(
                {
                    "file_path": c.get("file_path", "unknown"),
                    "line_number": c.get("line_number", 0),
                    "comment": c.get("text", ""),
                    "severity": "medium",
                    "issue_type": "bug",
                    "suggestion": None,
                }
            )

    # Extract metadata
    metadata = data.get("metadata", {})
    created_at_str = metadata.get("created_at")
    if created_at_str:
        try:
            analyzed_at = datetime.fromisoformat(created_at_str)
        except ValueError:
            analyzed_at = datetime.now()
    else:
        analyzed_at = datetime.now()

    # Determine method (auto-detect from body_top or metadata)
    if not method:
        body_top = data.get("body_top", "")
        if "OpenCode" in body_top or metadata.get("model") == "default":
            method = "opencode"
        else:
            method = "llm"

    # Determine model
    if not model:
        model = metadata.get("model", "unknown")

    # Build ReviewComment objects
    comments = []
    has_critical = False
    for issue in parsed_issues:
        severity_str = issue.get("severity", "medium").lower()
        try:
            severity = Severity(severity_str)
        except ValueError:
            severity = Severity.MEDIUM

        if severity == Severity.CRITICAL:
            has_critical = True

        issue_type_str = issue.get("issue_type", "bug").lower()
        try:
            issue_type = ReviewFocus(issue_type_str)
        except ValueError:
            issue_type = ReviewFocus.BUGS

        comments.append(
            ReviewComment(
                file_path=issue.get("file_path", "unknown"),
                line_number=issue.get("line_number", 0),
                message=issue.get("comment", issue.get("message", "")),
                severity=severity,
                issue_type=issue_type,
                suggestion=issue.get("suggestion"),
            )
        )

    # Build summary
    summary = data.get("unparsed_text", "")
    if not summary:
        # Try to extract from body_top
        body_top = data.get("body_top", "")
        if body_top:
            # Take first non-header paragraph
            lines = body_top.split("\n")
            for line in lines:
                if line and not line.startswith("#") and not line.startswith("**"):
                    summary = line[:200]
                    break
    if not summary:
        summary = f"Imported review with {len(comments)} issues"

    # Create ReviewResult
    result = ReviewResult(
        review_request_id=review_request_id,
        diff_revision=diff_revision,
        comments=comments,
        summary=summary,
        has_critical_issues=has_critical,
        analyzed_at=analyzed_at,
    )

    # Save to database
    try:
        analysis_id = review_db.save_analysis(
            result=result,
            repository=repository,
            analysis_method=method,
            model=model,
        )
        click.echo(f"Imported review as analysis #{analysis_id}")
        click.echo(f"  RR: #{review_request_id}")
        click.echo(f"  Repository: {repository}")
        click.echo(f"  Method: {method}")
        click.echo(f"  Model: {model}")
        click.echo(f"  Comments: {len(comments)}")
    except Exception as e:
        click.echo(f"Error saving to database: {e}", err=True)
        sys.exit(1)
