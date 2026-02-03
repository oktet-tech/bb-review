"""Textual application for interactive queue triage."""

from __future__ import annotations

import logging

from textual.app import App

from bb_review.db.queue_db import QueueDatabase
from bb_review.db.queue_models import QueueItem, QueueStatus

from .screens.queue_list import QueueListScreen


logger = logging.getLogger(__name__)


class QueueApp(App):
    """Interactive queue triage application."""

    TITLE = "BB Review Queue"

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(
        self,
        items: list[QueueItem],
        queue_db: QueueDatabase,
        filter_status: QueueStatus | None = None,
        exclude_statuses: list[QueueStatus] | None = None,
        filter_repo: str | None = None,
        filter_limit: int = 50,
    ):
        super().__init__()
        self.initial_items = items
        self.queue_db = queue_db
        self._filter_status = filter_status
        self._exclude_statuses = exclude_statuses
        self._filter_repo = filter_repo
        self._filter_limit = filter_limit

    def refresh_items(self) -> list[QueueItem]:
        """Re-query queue items with stored filters."""
        return self.queue_db.list_items(
            status=self._filter_status,
            exclude_statuses=self._exclude_statuses,
            repository=self._filter_repo,
            limit=self._filter_limit,
        )

    def on_mount(self) -> None:
        if not self.initial_items:
            self.notify("No queue items found", severity="error")
            self.exit()
            return

        self.push_screen(QueueListScreen(self.initial_items, self.queue_db))
