"""Review Board commenter for posting AI review results."""

import logging
from typing import Optional

from .analyzer import Analyzer
from .models import ReviewComment, ReviewResult, Severity
from .rb_client import ReviewBoardClient

logger = logging.getLogger(__name__)


class Commenter:
    """Posts AI review results to Review Board."""

    def __init__(
        self,
        rb_client: ReviewBoardClient,
        analyzer: Analyzer,
        auto_ship_it: bool = False,
    ):
        """Initialize the commenter.

        Args:
            rb_client: Review Board client.
            analyzer: Analyzer for formatting comments.
            auto_ship_it: Whether to auto "Ship It" when no issues found.
        """
        self.rb_client = rb_client
        self.analyzer = analyzer
        self.auto_ship_it = auto_ship_it

    def post_review(
        self,
        result: ReviewResult,
        dry_run: bool = False,
    ) -> Optional[int]:
        """Post review results to Review Board.

        Args:
            result: The review result to post.
            dry_run: If True, print what would be posted without actually posting.

        Returns:
            Review ID if posted, None if dry run.
        """
        review_request_id = result.review_request_id
        
        # Format the summary
        body_top = self.analyzer.format_review_summary(result)
        
        # Determine ship_it status
        ship_it = False
        if self.auto_ship_it and not result.comments and not result.has_critical_issues:
            ship_it = True
            body_top += "\n\n🚀 Auto-approved (no issues found)"

        # Format comments for posting
        comments = []
        for comment in result.comments:
            formatted_text = self.analyzer.format_comment_text(comment)
            comments.append({
                "file_path": comment.file_path,
                "line_number": comment.line_number,
                "text": formatted_text,
            })

        if dry_run:
            self._print_dry_run(review_request_id, body_top, comments, ship_it)
            return None

        # Post the review
        logger.info(
            f"Posting review to {review_request_id} with {len(comments)} comments"
        )
        
        try:
            review = self.rb_client.post_review(
                review_request_id=review_request_id,
                body_top=body_top,
                comments=comments,
                ship_it=ship_it,
                publish=True,
            )
            logger.info(f"Posted review {review.id} to request {review_request_id}")
            return review.id
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
        """Print what would be posted in a dry run.

        Args:
            review_request_id: Review request ID.
            body_top: Review summary.
            comments: Formatted comments.
            ship_it: Ship it status.
        """
        print("\n" + "=" * 60)
        print(f"DRY RUN - Review for request #{review_request_id}")
        print("=" * 60)
        
        print(f"\nShip It: {'Yes' if ship_it else 'No'}")
        
        print("\n--- Review Summary ---")
        print(body_top)
        
        if comments:
            print("\n--- Inline Comments ---")
            for i, comment in enumerate(comments, 1):
                print(f"\n[{i}] {comment['file_path']}:{comment['line_number']}")
                print("-" * 40)
                print(comment["text"])
        else:
            print("\n(No inline comments)")
        
        print("\n" + "=" * 60)

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
            lines.extend([
                "> ⚠️ **Warning:** Critical issues were found that should be addressed.",
                "",
            ])

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
            lines.extend([
                "## Result",
                "",
                "✅ No issues found in this review.",
                "",
            ])

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
