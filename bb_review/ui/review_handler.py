"""Review action handler extracted from ExportApp.

Plain class (not a widget) that orchestrates review actions: action picker,
delete, submit, export, comment picker callbacks. Operates on the app
via a stored reference.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from bb_review.db.models import AnalysisListItem
from bb_review.db.review_db import ReviewDatabase

from .models import ExportableAnalysis
from .screens.action_picker import (
    ActionPickerScreen,
    ActionResult,
    ActionType,
    ConfirmDeleteScreen,
    SubmitOptionsScreen,
)
from .screens.comment_picker import CommentPickerScreen
from .widgets.reviews_pane import ReviewsAction


if TYPE_CHECKING:
    from textual.app import App

    from bb_review.config import Config

logger = logging.getLogger(__name__)


class ReviewHandler:
    """Orchestrates review actions on behalf of the unified app.

    Args:
        app: The Textual App instance (for push_screen, notify, exit).
        db: ReviewDatabase instance.
        config: Optional config for RB submission.
        output_path: Optional export output path.
    """

    def __init__(
        self,
        app: App,
        db: ReviewDatabase,
        config: Config | None = None,
        output_path: str | None = None,
    ) -> None:
        self.app = app
        self.db = db
        self.config = config
        self.output_path = output_path
        self._batch_action_ids: list[int] = []
        self._pending_delete_ids: list[int] = []
        self._pending_submit_ids: list[int] = []

    # -- Public entry point --

    def handle_action(
        self,
        action: ReviewsAction,
        analyses: list[AnalysisListItem],
    ) -> None:
        """Dispatch a ReviewsAction from the ReviewsPane."""
        self._current_analyses = analyses

        if action.type == "batch_export":
            self._start_batch_export(action.ids or [])
        elif action.type == "batch_submit":
            self._submit_analyses(action.ids or [])
        elif action.type == "open_analysis":
            if action.analysis_id:
                self._start_batch_export([action.analysis_id])
        elif action.type == "single_action":
            self._batch_action_ids = []
            self._show_action_picker(action.analysis_id, analyses)
        elif action.type == "batch_action":
            self._batch_action_ids = action.ids or []
            if self._batch_action_ids:
                self._show_action_picker(self._batch_action_ids[0], analyses)

    # -- Batch export / comment picker --

    def _start_batch_export(self, selected_ids: list[int]) -> None:
        if not selected_ids:
            self.app.notify("No analyses selected", severity="warning")
            return

        exportable = []
        for analysis_id in selected_ids:
            full_analysis = self.db.get_analysis(analysis_id)
            if full_analysis:
                exportable.append(ExportableAnalysis.from_stored(full_analysis))

        if not exportable:
            self.app.notify("Failed to load analysis details", severity="error")
            return

        # Mark duplicate comments based on previously-dropped RB issues
        self._mark_duplicates(exportable)

        self.app.push_screen(
            CommentPickerScreen(exportable, db=self.db),
            callback=self._on_comments_picked,
        )

    def _mark_duplicates(self, exportable: list[ExportableAnalysis]) -> None:
        """Fetch dropped comments from RB and mark matching comments as duplicates."""
        if not self.config:
            return

        try:
            from bb_review.rr.dedup import fetch_dropped_comments
            from bb_review.rr.rb_client import ReviewBoardClient

            rb_client = ReviewBoardClient(
                url=self.config.reviewboard.url,
                bot_username=self.config.reviewboard.bot_username,
            )
            bot_username = self.config.reviewboard.bot_username

            for ea in exportable:
                rr_id = ea.analysis.review_request_id
                try:
                    dropped = fetch_dropped_comments(rb_client, rr_id, bot_username)
                    ea.mark_duplicates(dropped)
                except Exception:
                    logger.warning("Failed to fetch dropped comments for RR #%d", rr_id, exc_info=True)
        except Exception:
            logger.warning("Failed to connect to RB for dedup", exc_info=True)

    def _on_comments_picked(self, result) -> None:
        if result == "back":
            self._notify_refresh()
            return

        if not result:
            self._notify_refresh()
            return

        # Submit action: ("submit", analyses, option_str)
        if isinstance(result, tuple) and len(result) >= 2 and result[0] == "submit":
            analyses = result[1]
            option = result[2] if len(result) > 2 else "draft"
            if analyses:
                publish = option in ("publish", "ship_it")
                force_ship_it = option == "ship_it"
                self._submit_from_comment_picker(analyses[0], publish=publish, force_ship_it=force_ship_it)
            return

        self._do_export(result)

    # -- Action picker --

    def _show_action_picker(
        self,
        analysis_id: int | None,
        analyses: list[AnalysisListItem],
    ) -> None:
        if analysis_id is None:
            return

        analysis = next((a for a in analyses if a.id == analysis_id), None)
        if not analysis:
            self.app.notify(f"Analysis {analysis_id} not found", severity="error")
            return

        count = len(self._batch_action_ids) if self._batch_action_ids else 1
        self.app.push_screen(
            ActionPickerScreen(analysis, count=count),
            callback=self._on_action_picked,
        )

    def _on_action_picked(self, result: ActionResult | None) -> None:
        if not result:
            self._batch_action_ids = []
            return

        action_ids = self._batch_action_ids if self._batch_action_ids else [result.analysis_id]
        self._batch_action_ids = []

        if result.action == ActionType.EXPORT:
            self._start_batch_export(action_ids)
        elif result.action == ActionType.SUBMIT:
            self._submit_analyses(action_ids)
        elif result.action == ActionType.DELETE:
            self._delete_analyses(action_ids)
        elif result.action in (
            ActionType.MARK_DRAFT,
            ActionType.MARK_SUBMITTED,
            ActionType.MARK_OBSOLETE,
            ActionType.MARK_INVALID,
        ):
            status_map = {
                ActionType.MARK_DRAFT: "draft",
                ActionType.MARK_SUBMITTED: "submitted",
                ActionType.MARK_OBSOLETE: "obsolete",
                ActionType.MARK_INVALID: "invalid",
            }
            self._update_statuses(action_ids, status_map[result.action])

    # -- Delete --

    def _delete_analyses(self, analysis_ids: list[int]) -> None:
        if not analysis_ids:
            return

        self._pending_delete_ids = analysis_ids
        analysis = next((a for a in self._current_analyses if a.id == analysis_ids[0]), None)
        if analysis:
            self.app.push_screen(
                ConfirmDeleteScreen(analysis, count=len(analysis_ids)),
                callback=self._on_delete_confirmed,
            )

    def _on_delete_confirmed(self, confirmed: bool) -> None:
        analysis_ids = self._pending_delete_ids
        self._pending_delete_ids = []

        if confirmed and analysis_ids:
            deleted = 0
            for analysis_id in analysis_ids:
                if self.db.delete_analysis(analysis_id):
                    deleted += 1

            if deleted > 0:
                if deleted == 1:
                    self.app.notify(f"Deleted analysis #{analysis_ids[0]}", severity="information")
                else:
                    self.app.notify(f"Deleted {deleted} analyses", severity="information")
            else:
                self.app.notify("Failed to delete analyses", severity="error")

        self._notify_refresh()

    # -- Status update --

    def _update_statuses(self, analysis_ids: list[int], new_status: str) -> None:
        updated = 0
        for analysis_id in analysis_ids:
            try:
                self.db.update_status(analysis_id, new_status)
                updated += 1
            except ValueError:
                pass

        if updated > 0:
            if updated == 1:
                self.app.notify(f"Marked analysis #{analysis_ids[0]} as {new_status}")
            else:
                self.app.notify(f"Marked {updated} analyses as {new_status}")
        else:
            self.app.notify("Failed to update status", severity="error")

        self._notify_refresh()

    # -- Submit --

    def _submit_analyses(self, analysis_ids: list[int]) -> None:
        if not analysis_ids:
            return

        if not self.config:
            self.app.notify("Config not available for submission", severity="error")
            return

        self._pending_submit_ids = list(analysis_ids)
        self.app.push_screen(SubmitOptionsScreen(), callback=self._on_submit_option_chosen)

    def _on_submit_option_chosen(self, option: str | None) -> None:
        ids = self._pending_submit_ids
        self._pending_submit_ids = []

        if option is None or not ids:
            return

        publish = option in ("publish", "ship_it")
        force_ship_it = option == "ship_it"

        submissions = []
        for analysis_id in ids:
            entry = self._prepare_submission(analysis_id, force_ship_it)
            if entry:
                submissions.append(entry)

        if submissions:
            self.app.run_submit(submissions, publish=publish, force_ship_it=force_ship_it)

    def _prepare_submission(self, analysis_id: int, force_ship_it: bool) -> dict | None:
        """Build submission payload from a stored analysis. Returns None on error."""
        analysis = self.db.get_analysis(analysis_id)
        if not analysis:
            self.app.notify(f"Analysis #{analysis_id} not found", severity="error")
            return None

        from bb_review.models import ReviewComment, ReviewFocus, Severity

        comments = []
        severity_values = [s.value for s in Severity]
        focus_values = [f.value for f in ReviewFocus]
        for c in analysis.comments:
            sev = Severity(c.severity) if c.severity in severity_values else Severity.MEDIUM
            issue = ReviewFocus(c.issue_type) if c.issue_type in focus_values else ReviewFocus.BUGS
            comments.append(
                ReviewComment(
                    file_path=c.file_path,
                    line_number=c.line_number,
                    message=c.message,
                    severity=sev,
                    issue_type=issue,
                    suggestion=c.suggestion,
                )
            )

        if analysis.body_top:
            body_top = analysis.body_top
        else:
            body_parts = [f"## AI Code Review Summary\n\n{analysis.summary}"]
            if analysis.has_critical_issues:
                body_parts.append("\n**Note:** Critical issues found that require attention.")
            body_parts.append(f"\n\n*Analysis by {analysis.model_used} ({analysis.analysis_method.value})*")
            body_top = "\n".join(body_parts)

        inline_comments = []
        for c in comments:
            severity_label = c.severity.value.upper()
            text_parts = [f"**[{severity_label}] {c.issue_type.value.title()}**\n\n{c.message}"]
            if c.suggestion:
                text_parts.append(f"\n\n**Suggestion:** {c.suggestion}")
            inline_comments.append(
                {
                    "file_path": c.file_path,
                    "line_number": c.line_number,
                    "text": "\n".join(text_parts),
                }
            )

        return {
            "review_request_id": analysis.review_request_id,
            "body_top": body_top,
            "inline_comments": inline_comments,
            "ship_it": force_ship_it or (len(inline_comments) == 0 and not analysis.has_critical_issues),
            "analysis_id": analysis_id,
        }

    def _submit_from_comment_picker(
        self,
        exportable: ExportableAnalysis,
        publish: bool = False,
        force_ship_it: bool = False,
    ) -> None:
        """Submit an analysis with selected/edited comments from comment picker."""
        if not self.config:
            self.app.notify("Config not available for submission", severity="error")
            return

        analysis = exportable.analysis

        if analysis.body_top:
            body_top = analysis.body_top
        else:
            body_parts = [f"## AI Code Review Summary\n\n{analysis.summary}"]
            if analysis.has_critical_issues:
                body_parts.append("\n**Note:** Critical issues found that require attention.")
            body_parts.append(f"\n\n*Analysis by {analysis.model_used} ({analysis.analysis_method.value})*")
            body_top = "\n".join(body_parts)

        inline_comments = []
        for sc in exportable.comments:
            if not sc.selected:
                continue
            c = sc.comment
            severity_label = c.severity.upper()
            message = sc.effective_message
            text_parts = [f"**[{severity_label}] {c.issue_type.title()}**\n\n{message}"]
            suggestion = sc.effective_suggestion
            if suggestion:
                text_parts.append(f"\n\n**Suggestion:** {suggestion}")
            inline_comments.append(
                {
                    "file_path": c.file_path,
                    "line_number": c.line_number,
                    "text": "\n".join(text_parts),
                }
            )

        ship_it = force_ship_it or (len(inline_comments) == 0 and not analysis.has_critical_issues)
        self.app.run_submit(
            [
                {
                    "review_request_id": analysis.review_request_id,
                    "body_top": body_top,
                    "inline_comments": inline_comments,
                    "ship_it": ship_it,
                    "analysis_id": analysis.id,
                }
            ],
            publish=publish,
            force_ship_it=force_ship_it,
        )

    # -- Export --

    def _do_export(self, exported_analyses: list[ExportableAnalysis]) -> None:
        if not exported_analyses:
            self.app.notify("No analyses to export", severity="warning")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exported_files: list[str] = []

        for exportable in exported_analyses:
            export_data = self._build_export_data(exportable)
            rr_id = exportable.analysis.review_request_id

            if self.output_path and len(exported_analyses) == 1:
                output_file = Path(self.output_path)
            else:
                output_file = Path(f"export_review_{rr_id}_{timestamp}.json")

            try:
                with open(output_file, "w") as f:
                    json.dump(export_data, f, indent=2)
                exported_files.append(str(output_file))
            except Exception as e:
                self.app.notify(f"Export failed for RR #{rr_id}: {e}", severity="error")

        if exported_files:
            if len(exported_files) == 1:
                self.app.notify(f"Exported to {exported_files[0]}", severity="information")
            else:
                self.app.notify(f"Exported {len(exported_files)} files", severity="information")

        self._notify_refresh()

    def _build_export_data(self, exportable: ExportableAnalysis) -> dict:
        analysis = exportable.analysis
        body_top = self._format_body_top(exportable)

        comments = []
        for sel_comment in exportable.selected_comments:
            c = sel_comment.comment
            text = self._format_comment_text(sel_comment)
            comments.append({"file_path": c.file_path, "line_number": c.line_number, "text": text})

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
            "fake": analysis.fake,
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
        analysis = exportable.analysis
        stored_body = getattr(analysis, "body_top", None)
        if stored_body:
            return stored_body

        lines = ["## AI Code Review", ""]
        if exportable.include_summary:
            lines.append(f"**Summary**: {analysis.summary}")
            lines.append("")

        selected_comments = exportable.selected_comments
        if selected_comments:
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

        lines.append("---")
        lines.append(f"*Analyzed with {analysis.model_used} ({analysis.analysis_method.value})*")
        return "\n".join(lines)

    def _format_comment_text(self, sel_comment) -> str:
        c = sel_comment.comment
        lines = [
            f"**{c.issue_type.capitalize()}** ({c.severity.capitalize()})",
            "",
            sel_comment.effective_message,
        ]
        suggestion = sel_comment.effective_suggestion
        if suggestion:
            lines.append("")
            lines.append(f"**Suggestion:** {suggestion}")
        return "\n".join(lines)

    # -- Helpers --

    def _notify_refresh(self) -> None:
        """Tell the app to refresh the reviews pane data."""
        if hasattr(self.app, "refresh_reviews_pane"):
            self.app.refresh_reviews_pane()
