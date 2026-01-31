"""Main Textual application for interactive review management."""

from datetime import datetime
import json
from pathlib import Path

from textual.app import App

from bb_review.db.models import AnalysisListItem
from bb_review.db.review_db import ReviewDatabase

from .models import ExportableAnalysis
from .screens.action_picker import (
    ActionPickerScreen,
    ActionResult,
    ActionType,
    ConfirmDeleteScreen,
)
from .screens.analysis_list import AnalysisListResult, AnalysisListScreen
from .screens.comment_picker import CommentPickerScreen


class ExportApp(App):
    """Interactive review management application."""

    TITLE = "BB Review Interactive"

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
        # Filter params for refreshing
        filter_rr_id: int | None = None,
        filter_repo: str | None = None,
        filter_status: str | None = None,
        filter_chain_id: str | None = None,
        filter_limit: int = 50,
    ):
        """Initialize the interactive app.

        Args:
            analyses: List of AnalysisListItem to show for selection
            db: ReviewDatabase instance for loading full analysis details
            output_path: Optional output file path
            filter_rr_id: Filter by review request ID (for refresh)
            filter_repo: Filter by repository (for refresh)
            filter_status: Filter by status (for refresh)
            filter_chain_id: Filter by chain ID (for refresh)
            filter_limit: Maximum results (for refresh)
        """
        super().__init__()
        self.initial_analyses = analyses
        self.db = db
        self._batch_action_ids: list[int] = []  # Track IDs for batch actions
        self.output_path = output_path
        self.exported_analyses: list[ExportableAnalysis] = []
        # Store filter params for refresh
        self._filter_rr_id = filter_rr_id
        self._filter_repo = filter_repo
        self._filter_status = filter_status
        self._filter_chain_id = filter_chain_id
        self._filter_limit = filter_limit

    def _refresh_analyses(self) -> list[AnalysisListItem]:
        """Refresh the analysis list from database."""
        return self.db.list_analyses(
            review_request_id=self._filter_rr_id,
            repository=self._filter_repo,
            status=self._filter_status,
            chain_id=self._filter_chain_id,
            limit=self._filter_limit,
        )

    def _show_analysis_list(self) -> None:
        """Show the analysis list screen."""
        self.push_screen(
            AnalysisListScreen(self.initial_analyses),
            callback=self._on_analysis_list_result,
        )

    def on_mount(self) -> None:
        """Start the app by showing the analysis list."""
        if not self.initial_analyses:
            self.notify("No analyses found matching the filter", severity="error")
            self.exit()
            return

        self._show_analysis_list()

    def _on_analysis_list_result(self, result: AnalysisListResult | None) -> None:
        """Handle result from the analysis list screen.

        Args:
            result: AnalysisListResult or None if cancelled
        """
        if not result:
            self.exit()
            return

        if result.type == "batch_export":
            # Batch export selected analyses
            self._start_batch_export(result.ids or [])
        elif result.type == "single_action":
            # Show action picker for single analysis
            self._batch_action_ids = []
            self._show_action_picker(result.analysis_id)
        elif result.type == "batch_action":
            # Show action picker for batch (apply to all selected)
            self._batch_action_ids = result.ids or []
            if self._batch_action_ids:
                # Show picker using first analysis as representative
                self._show_action_picker(self._batch_action_ids[0])

    def _start_batch_export(self, selected_ids: list[int]) -> None:
        """Start batch export flow for selected analyses."""
        if not selected_ids:
            self.notify("No analyses selected", severity="warning")
            self._show_analysis_list()
            return

        # Load full analysis details
        exportable = []
        for analysis_id in selected_ids:
            full_analysis = self.db.get_analysis(analysis_id)
            if full_analysis:
                exportable.append(ExportableAnalysis.from_stored(full_analysis))

        if not exportable:
            self.notify("Failed to load analysis details", severity="error")
            self._show_analysis_list()
            return

        # Show comment picker
        self.push_screen(
            CommentPickerScreen(exportable, db=self.db),
            callback=self._on_comments_picked,
        )

    def _show_action_picker(self, analysis_id: int | None) -> None:
        """Show action picker for analysis/analyses."""
        if analysis_id is None:
            self._show_analysis_list()
            return

        # Find the analysis in the list
        analysis = next((a for a in self.initial_analyses if a.id == analysis_id), None)
        if not analysis:
            self.notify(f"Analysis {analysis_id} not found", severity="error")
            self._show_analysis_list()
            return

        # Pass count if batch action
        count = len(self._batch_action_ids) if self._batch_action_ids else 1
        self.push_screen(
            ActionPickerScreen(analysis, count=count),
            callback=self._on_action_picked,
        )

    def _on_action_picked(self, result: ActionResult | None) -> None:
        """Handle action picker result."""
        if not result:
            self._batch_action_ids = []
            self._show_analysis_list()
            return

        # Get the IDs to apply action to (batch or single)
        action_ids = self._batch_action_ids if self._batch_action_ids else [result.analysis_id]
        self._batch_action_ids = []  # Clear batch IDs

        if result.action == ActionType.EXPORT:
            # Export - go to comment picker
            self._start_batch_export(action_ids)
        elif result.action == ActionType.DELETE:
            # Delete with confirmation
            self._delete_analyses(action_ids)
        elif result.action in (
            ActionType.MARK_DRAFT,
            ActionType.MARK_SUBMITTED,
            ActionType.MARK_OBSOLETE,
            ActionType.MARK_INVALID,
        ):
            # Update status for all
            status_map = {
                ActionType.MARK_DRAFT: "draft",
                ActionType.MARK_SUBMITTED: "submitted",
                ActionType.MARK_OBSOLETE: "obsolete",
                ActionType.MARK_INVALID: "invalid",
            }
            new_status = status_map[result.action]
            self._update_statuses(action_ids, new_status)

    def _delete_analyses(self, analysis_ids: list[int]) -> None:
        """Delete analyses with confirmation."""
        if not analysis_ids:
            self._show_analysis_list()
            return

        # Store IDs for deletion callback
        self._pending_delete_ids = analysis_ids

        # Find first analysis for confirmation display
        analysis = next((a for a in self.initial_analyses if a.id == analysis_ids[0]), None)
        if analysis:
            self.push_screen(
                ConfirmDeleteScreen(analysis, count=len(analysis_ids)),
                callback=self._on_delete_confirmed,
            )
        else:
            self._show_analysis_list()

    def _on_delete_confirmed(self, confirmed: bool) -> None:
        """Handle delete confirmation result."""
        analysis_ids = getattr(self, "_pending_delete_ids", [])
        self._pending_delete_ids = []

        if confirmed and analysis_ids:
            deleted = 0
            for analysis_id in analysis_ids:
                if self.db.delete_analysis(analysis_id):
                    deleted += 1

            if deleted > 0:
                if deleted == 1:
                    self.notify(f"Deleted analysis #{analysis_ids[0]}", severity="information")
                else:
                    self.notify(f"Deleted {deleted} analyses", severity="information")

                # Refresh and show list
                self.initial_analyses = self._refresh_analyses()
                if not self.initial_analyses:
                    self.notify("No analyses remaining", severity="warning")
                    self.exit()
                    return
            else:
                self.notify("Failed to delete analyses", severity="error")

        self._show_analysis_list()

    def _update_statuses(self, analysis_ids: list[int], new_status: str) -> None:
        """Update status for multiple analyses and refresh list."""
        updated = 0
        for analysis_id in analysis_ids:
            try:
                self.db.update_status(analysis_id, new_status)
                updated += 1
            except ValueError:
                pass  # Skip invalid

        if updated > 0:
            if updated == 1:
                self.notify(f"Marked analysis #{analysis_ids[0]} as {new_status}")
            else:
                self.notify(f"Marked {updated} analyses as {new_status}")

            # Refresh and show list
            self.initial_analyses = self._refresh_analyses()
            if not self.initial_analyses:
                self.notify("No analyses remaining after filter", severity="warning")
                self.exit()
                return
        else:
            self.notify("Failed to update status", severity="error")

        self._show_analysis_list()

    def _on_comments_picked(self, result) -> None:
        """Handle picked comments from the comment picker screen.

        Args:
            result: List of analyses, "back" to return to selection, or None if cancelled
        """
        if result == "back":
            # Go back to analysis selection
            self._show_analysis_list()
            return

        if not result:
            self.exit()
            return

        self.exported_analyses = result
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
            Dict in submission-ready format (also re-importable)
        """
        analysis = exportable.analysis

        # Format body_top for RB submission
        body_top = self._format_body_top(exportable)

        # Format comments for RB submission
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

        # Build parsed_issues with full structured data for re-import
        parsed_issues = []
        for sel_comment in exportable.selected_comments:
            c = sel_comment.comment
            parsed_issues.append(
                {
                    "file_path": c.file_path,
                    "line_number": c.line_number,
                    "severity": c.severity,
                    "issue_type": c.issue_type,
                    "comment": sel_comment.effective_message,
                    "suggestion": sel_comment.effective_suggestion,
                }
            )

        return {
            "review_request_id": analysis.review_request_id,
            "repository": analysis.repository,
            "body_top": body_top,
            "comments": comments,
            "ship_it": len(comments) == 0 and not analysis.has_critical_issues,
            "summary": analysis.summary,
            "has_critical_issues": analysis.has_critical_issues,
            "parsed_issues": parsed_issues,
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
