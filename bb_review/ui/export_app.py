"""Main Textual application for interactive export."""

from datetime import datetime
import json
from pathlib import Path

from textual.app import App

from bb_review.db.models import AnalysisListItem
from bb_review.db.review_db import ReviewDatabase

from .models import ExportableAnalysis
from .screens.analysis_list import AnalysisListScreen
from .screens.comment_picker import CommentPickerScreen


class ExportApp(App):
    """Interactive export application."""

    TITLE = "BB Review Export"

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(
        self,
        analyses: list[AnalysisListItem],
        db: ReviewDatabase,
        output_path: str | None = None,
    ):
        """Initialize the export app.

        Args:
            analyses: List of AnalysisListItem to show for selection
            db: ReviewDatabase instance for loading full analysis details
            output_path: Optional output file path
        """
        super().__init__()
        self.initial_analyses = analyses
        self.db = db
        self.output_path = output_path
        self.exported_analyses: list[ExportableAnalysis] = []

    def on_mount(self) -> None:
        """Start the app by showing the analysis list."""
        if not self.initial_analyses:
            self.notify("No analyses found matching the filter", severity="error")
            self.exit()
            return

        self.push_screen(
            AnalysisListScreen(self.initial_analyses),
            callback=self._on_analyses_selected,
        )

    def _on_analyses_selected(self, selected_ids: list[int] | None) -> None:
        """Handle selected analyses from the list screen.

        Args:
            selected_ids: List of selected analysis IDs, or None if cancelled
        """
        if not selected_ids:
            self.exit()
            return

        # Load full analysis details
        exportable = []
        for analysis_id in selected_ids:
            full_analysis = self.db.get_analysis(analysis_id)
            if full_analysis:
                exportable.append(ExportableAnalysis.from_stored(full_analysis))

        if not exportable:
            self.notify("Failed to load analysis details", severity="error")
            self.exit()
            return

        # Show comment picker
        self.push_screen(
            CommentPickerScreen(exportable),
            callback=self._on_comments_picked,
        )

    def _on_comments_picked(self, analyses: list[ExportableAnalysis] | None) -> None:
        """Handle picked comments from the comment picker screen.

        Args:
            analyses: List of analyses with selected comments, or None if cancelled
        """
        if not analyses:
            self.exit()
            return

        self.exported_analyses = analyses
        self._do_export()

    def _do_export(self) -> None:
        """Perform the export to JSON."""
        if not self.exported_analyses:
            self.notify("No analyses to export", severity="warning")
            self.exit()
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exported_files: list[str] = []

        # Export each analysis to a separate file
        for exportable in self.exported_analyses:
            export_data = self._build_export_data(exportable)
            rr_id = exportable.analysis.review_request_id

            # Determine output path
            if self.output_path and len(self.exported_analyses) == 1:
                # Use provided path only for single export
                output_file = Path(self.output_path)
            else:
                # Generate filename: export_review_<RR_ID>_<date>_<time>.json
                output_file = Path(f"export_review_{rr_id}_{timestamp}.json")

            try:
                with open(output_file, "w") as f:
                    json.dump(export_data, f, indent=2)
                exported_files.append(str(output_file))
            except Exception as e:
                self.notify(f"Export failed for RR #{rr_id}: {e}", severity="error")

        # Log exported files
        if exported_files:
            self.log_exports(exported_files)

        self.exit()

    def log_exports(self, files: list[str]) -> None:
        """Log the exported files to console."""
        import click

        click.echo("\nExported reviews:")
        for f in files:
            click.echo(f"  - {f}")
        click.echo(f"\nTotal: {len(files)} file(s)")

    def _build_export_data(self, exportable: ExportableAnalysis) -> dict:
        """Build export data for a single analysis.

        Args:
            exportable: The exportable analysis with selected comments

        Returns:
            Dict in submission-ready format
        """
        analysis = exportable.analysis

        # Format body_top
        body_top = self._format_body_top(exportable)

        # Format selected comments
        comments = []
        for sel_comment in exportable.selected_comments:
            c = sel_comment.comment
            text = self._format_comment_text(sel_comment)
            comments.append(
                {
                    "file_path": c.file_path,
                    "line_number": c.line_number,
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

    def _format_body_top(self, exportable: ExportableAnalysis) -> str:
        """Format the body_top section.

        Args:
            exportable: The exportable analysis

        Returns:
            Formatted body_top string
        """
        analysis = exportable.analysis
        lines = []

        lines.append("## AI Code Review")
        lines.append("")

        if exportable.include_summary:
            lines.append(f"**Summary**: {analysis.summary}")
            lines.append("")

        selected_comments = exportable.selected_comments
        if selected_comments:
            # Severity breakdown
            severity_counts: dict[str, int] = {}
            for sel_comment in selected_comments:
                sev = sel_comment.comment.severity.capitalize()
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

    def _format_comment_text(self, sel_comment) -> str:
        """Format a single comment for export.

        Args:
            sel_comment: The selectable comment

        Returns:
            Formatted comment text
        """
        c = sel_comment.comment
        lines = []

        # Header with severity and type
        issue_type = c.issue_type.capitalize()
        severity = c.severity.capitalize()
        lines.append(f"**{issue_type}** ({severity})")
        lines.append("")

        # Message (use edited if available)
        lines.append(sel_comment.effective_message)

        # Suggestion (use edited if available)
        suggestion = sel_comment.effective_suggestion
        if suggestion:
            lines.append("")
            lines.append(f"**Suggestion:** {suggestion}")

        return "\n".join(lines)
