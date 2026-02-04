"""Review Board commenter for posting AI review results."""

import logging

import click

from ..models import ReviewComment, ReviewResult, Severity
from .rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)


class Commenter:
    """Posts AI review results to Review Board."""

    def __init__(
        self,
        rb_client: ReviewBoardClient,
        auto_ship_it: bool = False,
    ):
        """Initialize the commenter.

        Args:
            rb_client: Review Board client.
            auto_ship_it: Whether to auto "Ship It" when no issues found.
        """
        self.rb_client = rb_client
        self.auto_ship_it = auto_ship_it

    def post_review(
        self,
        result: ReviewResult,
        dry_run: bool = False,
    ) -> int | None:
        """Post review results to Review Board.

        Args:
            result: The review result to post.
            dry_run: If True, print what would be posted without actually posting.

        Returns:
            Review ID if posted, None if dry run.
        """
        review_request_id = result.review_request_id

        # Format the summary
        body_top = ReviewFormatter.format_review_summary(result)

        # Determine ship_it status
        ship_it = False
        if self.auto_ship_it and not result.comments and not result.has_critical_issues:
            ship_it = True
            body_top += "\n\nAuto-approved (no issues found)"

        # Format comments for posting
        comments = []
        for comment in result.comments:
            formatted_text = ReviewFormatter.format_comment_text(comment)
            comments.append(
                {
                    "file_path": comment.file_path,
                    "line_number": comment.line_number,
                    "text": formatted_text,
                }
            )

        if dry_run:
            self._print_dry_run(review_request_id, body_top, comments, ship_it)
            return None

        # Post the review
        logger.info(f"Posting review to {review_request_id} with {len(comments)} comments")

        try:
            review = self.rb_client.post_review(
                review_request_id=review_request_id,
                body_top=body_top,
                comments=comments,
                ship_it=ship_it,
                publish=True,
            )
            review_id = review.get("id") if isinstance(review, dict) else review.id
            logger.info(f"Posted review {review_id} to request {review_request_id}")
            return review_id
        except Exception as e:
            logger.error(f"Failed to post review: {e}")
            raise

    def _print_dry_run(
        self,
        review_request_id: int,
        body_top: str,
        comments: list[dict],
        ship_it: bool,
    ) -> None:
        """Print what would be posted in a dry run."""
        click.echo("\n" + "=" * 60)
        click.echo(f"DRY RUN - Review for request #{review_request_id}")
        click.echo("=" * 60)

        click.echo(f"\nShip It: {'Yes' if ship_it else 'No'}")

        click.echo("\n--- Review Summary ---")
        click.echo(body_top)

        if comments:
            click.echo("\n--- Inline Comments ---")
            for i, comment in enumerate(comments, 1):
                click.echo(f"\n[{i}] {comment['file_path']}:{comment['line_number']}")
                click.echo("-" * 40)
                click.echo(comment["text"])
        else:
            click.echo("\n(No inline comments)")

        click.echo("\n" + "=" * 60)

    def format_cli_output(self, result: ReviewResult) -> str:
        """Format review result for CLI output.

        Args:
            result: The review result.

        Returns:
            Formatted string for terminal output.
        """
        lines = [
            f"Review Analysis for Request #{result.review_request_id}",
            f"Diff Revision: {result.diff_revision}",
            f"Analyzed at: {result.analyzed_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "Summary:",
            result.summary,
            "",
        ]

        if result.has_critical_issues:
            lines.append("⚠️  CRITICAL ISSUES FOUND")
            lines.append("")

        if result.comments:
            lines.append(f"Issues Found: {len(result.comments)}")
            lines.append("")

            # Group by severity
            by_severity: dict[Severity, list[ReviewComment]] = {}
            for comment in result.comments:
                if comment.severity not in by_severity:
                    by_severity[comment.severity] = []
                by_severity[comment.severity].append(comment)

            for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
                if severity not in by_severity:
                    continue

                lines.append(f"### {severity.value.upper()} ({len(by_severity[severity])})")
                lines.append("")

                for comment in by_severity[severity]:
                    lines.append(f"  {comment.file_path}:{comment.line_number}")
                    lines.append(f"    Type: {comment.issue_type.value}")
                    lines.append(f"    {comment.message}")
                    if comment.suggestion:
                        lines.append(f"    Suggestion: {comment.suggestion}")
                    lines.append("")
        else:
            lines.append("✅ No issues found")

        return "\n".join(lines)


class ReviewFormatter:
    """Utility class for formatting review content."""

    @staticmethod
    def format_comment_text(comment: ReviewComment) -> str:
        """Format a review comment for posting to Review Board."""
        severity_labels = {
            Severity.LOW: "[INFO]",
            Severity.MEDIUM: "[WARNING]",
            Severity.HIGH: "[HIGH]",
            Severity.CRITICAL: "[CRITICAL]",
        }

        label = severity_labels.get(comment.severity, "[--]")
        severity = comment.severity.value.upper()
        issue_type = comment.issue_type.value
        parts = [
            f"{label} **{severity}** ({issue_type})",
            "",
            comment.message,
        ]

        if comment.suggestion:
            parts.extend(["", "**Suggestion:**", comment.suggestion])

        return "\n".join(parts)

    @staticmethod
    def format_review_summary(result: ReviewResult) -> str:
        """Format the overall review summary for posting to Review Board."""
        if not result.comments:
            return f"**AI Review Complete**\n\n{result.summary}\n\nNo issues found."

        severity_counts: dict[Severity, int] = {}
        for c in result.comments:
            severity_counts[c.severity] = severity_counts.get(c.severity, 0) + 1

        parts = ["**AI Review Complete**", "", result.summary, "", "**Issue Summary:**"]

        for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
            count = severity_counts.get(severity, 0)
            if count:
                parts.append(f"- {severity.value.capitalize()}: {count}")

        if result.has_critical_issues:
            parts.extend(["", "**Critical issues found. Please address before merging.**"])

        return "\n".join(parts)

    @staticmethod
    def format_as_markdown(result: ReviewResult) -> str:
        """Format review result as Markdown.

        Args:
            result: The review result.

        Returns:
            Markdown formatted string.
        """
        lines = [
            f"# AI Code Review - Request #{result.review_request_id}",
            "",
            f"**Diff Revision:** {result.diff_revision}",
            f"**Analyzed:** {result.analyzed_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Summary",
            "",
            result.summary,
            "",
        ]

        if result.has_critical_issues:
            lines.extend(
                [
                    "> ⚠️ **Warning:** Critical issues were found that should be addressed.",
                    "",
                ]
            )

        if result.comments:
            lines.append("## Issues")
            lines.append("")

            # Group by file
            by_file: dict[str, list[ReviewComment]] = {}
            for comment in result.comments:
                if comment.file_path not in by_file:
                    by_file[comment.file_path] = []
                by_file[comment.file_path].append(comment)

            for file_path, comments in by_file.items():
                lines.append(f"### `{file_path}`")
                lines.append("")

                for comment in sorted(comments, key=lambda c: c.line_number):
                    severity_badge = {
                        Severity.LOW: "🔵",
                        Severity.MEDIUM: "🟡",
                        Severity.HIGH: "🟠",
                        Severity.CRITICAL: "🔴",
                    }.get(comment.severity, "•")

                    lines.append(
                        f"- **Line {comment.line_number}** {severity_badge} "
                        f"[{comment.severity.value}] ({comment.issue_type.value})"
                    )
                    lines.append(f"  - {comment.message}")
                    if comment.suggestion:
                        lines.append(f"  - 💡 *Suggestion:* {comment.suggestion}")
                    lines.append("")
        else:
            lines.extend(
                [
                    "## Result",
                    "",
                    "✅ No issues found in this review.",
                    "",
                ]
            )

        return "\n".join(lines)

    @staticmethod
    def format_as_json(result: ReviewResult) -> dict:
        """Format review result as JSON-serializable dict.

        Args:
            result: The review result.

        Returns:
            Dictionary representation.
        """
        return {
            "review_request_id": result.review_request_id,
            "diff_revision": result.diff_revision,
            "analyzed_at": result.analyzed_at.isoformat(),
            "summary": result.summary,
            "has_critical_issues": result.has_critical_issues,
            "issue_count": result.issue_count,
            "comments": [
                {
                    "file_path": c.file_path,
                    "line_number": c.line_number,
                    "message": c.message,
                    "severity": c.severity.value,
                    "issue_type": c.issue_type.value,
                    "suggestion": c.suggestion,
                }
                for c in result.comments
            ],
        }

    @staticmethod
    def format_for_submission(
        review_request_id: int,
        body_top: str,
        comments: list[dict],
        ship_it: bool = False,
        unparsed_text: str = "",
        parsed_issues: list[dict] | None = None,
        metadata: dict | None = None,
        rr_summary: str | None = None,
    ) -> dict:
        """Format review data for submission JSON file.

        This creates a JSON structure that can be saved to a file,
        edited by the user, and then submitted via the 'submit' command.

        Args:
            review_request_id: Review Board request ID.
            body_top: Formatted review body/summary.
            comments: List of inline comments in RB API format.
            ship_it: Whether to mark as "Ship It".
            unparsed_text: Any unparsed text from LLM output (for user review).
            parsed_issues: Optional list of parsed issues for reference.
            metadata: Optional metadata dict (model, analyzed_at, etc.).
            rr_summary: Optional review request summary from Review Board.

        Returns:
            Dictionary ready for JSON serialization and submission.
        """
        from datetime import datetime

        result = {
            "review_request_id": review_request_id,
            "body_top": body_top,
            "comments": comments,
            "ship_it": ship_it,
            "unparsed_text": unparsed_text,
        }

        if rr_summary:
            result["rr_summary"] = rr_summary

        if parsed_issues is not None:
            result["parsed_issues"] = parsed_issues

        result["metadata"] = metadata or {
            "created_at": datetime.now().isoformat(),
            "dry_run": True,
        }

        return result
