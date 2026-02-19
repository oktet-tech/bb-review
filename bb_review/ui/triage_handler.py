"""Triage action handler for the unified TUI.

Orchestrates triage actions: open, export, delete, status changes.
Mirrors the pattern of review_handler.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bb_review.db.models import TriageListItem
from bb_review.db.review_db import ReviewDatabase

from .screens.triage_action_picker import (
    TriageActionPickerScreen,
    TriageActionResult,
    TriageActionType,
)
from .screens.triage_export_screen import TriageExportScreen
from .widgets.work_pane import WorkAction


if TYPE_CHECKING:
    from textual.app import App

    from bb_review.config import Config


logger = logging.getLogger(__name__)


class TriageHandler:
    """Orchestrates triage actions on behalf of the unified app."""

    def __init__(
        self,
        app: App,
        db: ReviewDatabase,
        config: Config | None = None,
    ) -> None:
        self.app = app
        self.db = db
        self.config = config
        self._batch_action_ids: list[int] = []
        self._pending_delete_ids: list[int] = []

    def handle_action(
        self,
        action: WorkAction,
        items: list[TriageListItem],
    ) -> None:
        """Dispatch a WorkAction from the WorkPane."""
        self._current_items = items

        if action.type == "open":
            if action.triage_id:
                self._open_triage(action.triage_id)
        elif action.type == "single_action":
            self._batch_action_ids = []
            self._show_action_picker(action.triage_id, items)
        elif action.type == "batch_action":
            self._batch_action_ids = action.ids or []
            if self._batch_action_ids:
                self._show_action_picker(self._batch_action_ids[0], items)
        elif action.type == "batch_delete":
            self._delete_triages(action.ids or [])

    # -- Open triage session --

    def _open_triage(self, triage_id: int) -> None:
        """Load a triage session from DB and push TriageViewScreen."""
        session = self.db.get_triage(triage_id)
        if not session:
            self.app.notify(f"Triage #{triage_id} not found", severity="error")
            return

        from .screens.triage_view_screen import TriageViewScreen

        self.app.push_screen(
            TriageViewScreen.from_stored(
                session,
                config=self.config,
                db=self.db,
            ),
            self._on_triage_view_dismissed,
        )

    def _on_triage_view_dismissed(self, result: str | None) -> None:
        self._notify_refresh()

    # -- Action picker --

    def _show_action_picker(
        self,
        triage_id: int | None,
        items: list[TriageListItem],
    ) -> None:
        if triage_id is None:
            return

        item = next((i for i in items if i.id == triage_id), None)
        if not item:
            self.app.notify(f"Triage #{triage_id} not found", severity="error")
            return

        count = len(self._batch_action_ids) if self._batch_action_ids else 1
        self.app.push_screen(
            TriageActionPickerScreen(item, count=count),
            callback=self._on_action_picked,
        )

    def _on_action_picked(self, result: TriageActionResult | None) -> None:
        if not result:
            self._batch_action_ids = []
            return

        action_ids = self._batch_action_ids if self._batch_action_ids else [result.triage_id]
        self._batch_action_ids = []

        if result.action == TriageActionType.OPEN:
            if action_ids:
                self._open_triage(action_ids[0])
        elif result.action == TriageActionType.EXPORT:
            self._export_triages(action_ids)
        elif result.action == TriageActionType.DELETE:
            self._delete_triages(action_ids)
        elif result.action in (
            TriageActionType.MARK_DRAFT,
            TriageActionType.MARK_REVIEWED,
            TriageActionType.MARK_EXPORTED,
        ):
            status_map = {
                TriageActionType.MARK_DRAFT: "draft",
                TriageActionType.MARK_REVIEWED: "reviewed",
                TriageActionType.MARK_EXPORTED: "exported",
            }
            self._update_statuses(action_ids, status_map[result.action])

    # -- Export --

    def _export_triages(self, triage_ids: list[int]) -> None:
        if not triage_ids:
            return

        sessions = []
        for tid in triage_ids:
            session = self.db.get_triage(tid)
            if session:
                sessions.append(session)

        if not sessions:
            self.app.notify("Failed to load triage sessions", severity="error")
            return

        self.app.push_screen(
            TriageExportScreen(sessions),
            callback=self._on_export_done,
        )

    def _on_export_done(self, result: str | None) -> None:
        if result:
            self.app.notify(result, severity="information")
            # Mark exported sessions
            for item in getattr(self, "_current_items", []):
                if item.id in self._batch_action_ids:
                    try:
                        self.db.update_triage_status(item.id, "exported")
                    except ValueError:
                        pass
        self._notify_refresh()

    # -- Delete --

    def _delete_triages(self, triage_ids: list[int]) -> None:
        if not triage_ids:
            return

        self._pending_delete_ids = triage_ids
        item = next((i for i in self._current_items if i.id == triage_ids[0]), None)
        if item:
            from .screens.triage_action_picker import ConfirmTriageDeleteScreen

            self.app.push_screen(
                ConfirmTriageDeleteScreen(item, count=len(triage_ids)),
                callback=self._on_delete_confirmed,
            )

    def _on_delete_confirmed(self, confirmed: bool) -> None:
        ids = self._pending_delete_ids
        self._pending_delete_ids = []

        if confirmed and ids:
            deleted = 0
            for tid in ids:
                if self.db.delete_triage(tid):
                    deleted += 1
            if deleted > 0:
                label = f"Deleted triage #{ids[0]}" if deleted == 1 else f"Deleted {deleted} triages"
                self.app.notify(label, severity="information")
            else:
                self.app.notify("Failed to delete triages", severity="error")

        self._notify_refresh()

    # -- Status update --

    def _update_statuses(self, triage_ids: list[int], new_status: str) -> None:
        updated = 0
        for tid in triage_ids:
            try:
                self.db.update_triage_status(tid, new_status)
                updated += 1
            except ValueError:
                pass

        if updated > 0:
            label = (
                f"Marked triage #{triage_ids[0]} as {new_status}"
                if updated == 1
                else f"Marked {updated} triages as {new_status}"
            )
            self.app.notify(label)
        else:
            self.app.notify("Failed to update status", severity="error")

        self._notify_refresh()

    # -- Helpers --

    def _notify_refresh(self) -> None:
        if hasattr(self.app, "refresh_work_pane"):
            self.app.refresh_work_pane()
