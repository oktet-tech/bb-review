"""Export functionality for reviews database."""

from datetime import datetime

from .models import StoredAnalysis, StoredChain


def export_to_json(analysis: StoredAnalysis) -> dict:
    """Export an analysis to submission-ready JSON format.

    This format is compatible with `bb-review submit` command.

    Args:
        analysis: The stored analysis to export

    Returns:
        Dict in submission format with body_top, comments, etc.
    """
    # Format body_top (summary section)
    body_top = _format_body_top(analysis)

    # Format comments
    comments = []
    for comment in analysis.comments:
        text = _format_comment_text(comment)
        comments.append(
            {
                "file_path": comment.file_path,
                "line_number": comment.line_number,
                "text": text,
            }
        )

    return {
        "review_request_id": analysis.review_request_id,
        "body_top": body_top,
        "comments": comments,
        "ship_it": len(comments) == 0 and not analysis.has_critical_issues,
        "metadata": {
            "analysis_id": analysis.id,
            "diff_revision": analysis.diff_revision,
            "analyzed_at": analysis.analyzed_at.isoformat(),
            "model": analysis.model_used,
            "method": analysis.analysis_method.value,
        },
    }


def export_to_markdown(analysis: StoredAnalysis) -> str:
    """Export an analysis to human-readable Markdown format.

    Args:
        analysis: The stored analysis to export

    Returns:
        Formatted Markdown string
    """
    lines = []

    # Header
    lines.append(f"# Code Review: RR #{analysis.review_request_id}")
    lines.append("")

    # Metadata
    lines.append(f"**Repository**: {analysis.repository}")
    lines.append(f"**Diff Revision**: {analysis.diff_revision}")
    if analysis.base_commit_id:
        lines.append(f"**Base Commit**: `{analysis.base_commit_id[:12]}`")
    lines.append(f"**Analyzed**: {analysis.analyzed_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Model**: {analysis.model_used}")
    lines.append(f"**Method**: {analysis.analysis_method.value}")
    lines.append(f"**Status**: {analysis.status.value}")
    if analysis.rr_summary:
        lines.append(f"**RR Summary**: {analysis.rr_summary}")
    if analysis.submitter:
        lines.append(f"**Submitter**: {analysis.submitter}")
    if analysis.chain_id:
        lines.append(f"**Chain**: {analysis.chain_id} (position {analysis.chain_position})")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(analysis.summary)
    lines.append("")

    # Issue stats
    if analysis.comments:
        severity_counts = {}
        for comment in analysis.comments:
            sev = comment.severity.capitalize()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        stats = ", ".join(f"{count} {sev.lower()}" for sev, count in sorted(severity_counts.items()))
        lines.append(f"**Issues Found**: {len(analysis.comments)} ({stats})")
        if analysis.has_critical_issues:
            lines.append("**Warning**: Contains critical issues!")
        lines.append("")
    else:
        lines.append("**No issues found.**")
        lines.append("")

    # Comments
    if analysis.comments:
        lines.append("## Comments")
        lines.append("")

        # Group by file
        by_file: dict[str, list] = {}
        for comment in analysis.comments:
            if comment.file_path not in by_file:
                by_file[comment.file_path] = []
            by_file[comment.file_path].append(comment)

        for file_path, file_comments in sorted(by_file.items()):
            lines.append(f"### {file_path}")
            lines.append("")

            for comment in sorted(file_comments, key=lambda c: c.line_number):
                issue_type = comment.issue_type.capitalize()
                severity = comment.severity.capitalize()
                lines.append(f"#### Line {comment.line_number} - {issue_type} ({severity})")
                lines.append("")
                lines.append(comment.message)
                lines.append("")
                if comment.suggestion:
                    lines.append(f"**Suggestion:** {comment.suggestion}")
                    lines.append("")
                lines.append("---")
                lines.append("")

    # Footer with export info
    lines.append("---")
    lines.append(f"*Exported from BB Review database (analysis ID: {analysis.id})*")
    lines.append(f"*Export time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


def export_chain_to_markdown(chain: StoredChain) -> str:
    """Export a chain of analyses to Markdown format.

    Args:
        chain: The stored chain to export

    Returns:
        Formatted Markdown string
    """
    lines = []

    # Header
    lines.append(f"# Chain Review: {chain.chain_id}")
    lines.append("")

    # Chain metadata
    lines.append(f"**Repository**: {chain.repository}")
    lines.append(f"**Created**: {chain.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Reviews**: {chain.reviewed_count}")
    lines.append(f"**Total Issues**: {chain.total_issues}")
    if chain.partial:
        lines.append(f"**Status**: Partial (failed at RR #{chain.failed_at_rr_id})")
    else:
        lines.append("**Status**: Complete")
    if chain.branch_name:
        lines.append(f"**Branch**: {chain.branch_name}")
    lines.append("")

    # Individual reviews
    lines.append("## Reviews")
    lines.append("")

    for analysis in chain.analyses:
        lines.append(f"### RR #{analysis.review_request_id} (Diff {analysis.diff_revision})")
        lines.append("")
        if analysis.rr_summary:
            lines.append(f"*{analysis.rr_summary}*")
            lines.append("")
        lines.append(analysis.summary)
        lines.append("")
        lines.append(f"Issues: {analysis.issue_count}")
        lines.append("")

        if analysis.comments:
            for comment in analysis.comments:
                severity = comment.severity.capitalize()
                msg_preview = comment.message[:80] + "..." if len(comment.message) > 80 else comment.message
                lines.append(f"- **{comment.file_path}:{comment.line_number}** ({severity}): {msg_preview}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _format_body_top(analysis: StoredAnalysis) -> str:
    """Format the body_top section for Review Board submission."""
    lines = []

    lines.append("## AI Code Review")
    lines.append("")
    lines.append(f"**Summary**: {analysis.summary}")
    lines.append("")

    if analysis.comments:
        # Severity breakdown
        severity_counts = {}
        for comment in analysis.comments:
            sev = comment.severity.capitalize()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        lines.append("**Issues Found:**")
        for severity in ["Critical", "High", "Medium", "Low"]:
            if severity in severity_counts:
                lines.append(f"- {severity}: {severity_counts[severity]}")
        lines.append("")

        if analysis.has_critical_issues:
            lines.append("> **Warning**: This review contains critical issues that should be addressed.")
            lines.append("")
    else:
        lines.append("No issues found. Code looks good!")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Analyzed with {analysis.model_used} ({analysis.analysis_method.value})*")

    return "\n".join(lines)


def _format_comment_text(comment) -> str:
    """Format a single comment for Review Board submission."""
    lines = []

    # Header with severity and type
    issue_type = comment.issue_type.capitalize()
    severity = comment.severity.capitalize()
    lines.append(f"**{issue_type}** ({severity})")
    lines.append("")

    # Message
    lines.append(comment.message)

    # Suggestion
    if comment.suggestion:
        lines.append("")
        lines.append(f"**Suggestion:** {comment.suggestion}")

    return "\n".join(lines)
