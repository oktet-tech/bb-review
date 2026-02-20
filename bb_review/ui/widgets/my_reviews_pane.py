"""My Reviews pane widget for the user's own submitted RRs."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import DataTable, Label

from bb_review.db.queue_db import QueueDatabase
from bb_review.db.queue_models import QueueItem, QueueStatus


STATUS_ICONS: dict[str, str] = {
    "todo": "T",
    "next": "N",
    "in_progress": "P",
    "done": "D",
    "failed": "F",
    "ignore": "I",
}

DIM_STATUSES = {QueueStatus.IGNORE, QueueStatus.DONE}


class MyReviewsPane(Container):
    """Pane showing the current user's own submitted review requests."""

    # -- Messages posted to the parent app --

    class SyncRequested(Message):
        """User pressed S to sync from RB."""

    class ProcessRequested(Message):
        """User pressed R to process next items."""

    class TriageRequested(Message):
        """User wants to triage comments on an RR."""

        def __init__(self, rr_id: int) -> None:
            super().__init__()
            self.rr_id = rr_id

    # -- Bindings --

    BINDINGS = [
        Binding("space", "toggle_selection", "Toggle Select"),
        Binding("a", "toggle_all", "Select All"),
        Binding("n", "mark_next", "Next"),
        Binding("i", "mark_ignore", "Ignore"),
        Binding("t", "triage_item", "Triage"),
        Binding("x", "show_actions", "Actions"),
        Binding("ctrl+r", "request_sync", "Sync"),
        Binding("g", "request_sync", "Sync", show=False),
        Binding("ctrl+enter", "request_process", "Process"),
    ]

    DEFAULT_CSS = """
    MyReviewsPane {
        height: 1fr;
    }

    MyReviewsPane #my-reviews-header-container {
        height: auto;
        padding: 1;
        background: $surface;
    }

    MyReviewsPane #my-reviews-title {
        text-style: bold;
        color: $text;
    }

    MyReviewsPane #my-reviews-table-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    MyReviewsPane #my-reviews-status-bar {
        height: auto;
        padding: 1;
        background: $surface;
        dock: bottom;
    }

    MyReviewsPane DataTable {
        height: 100%;
    }

    MyReviewsPane DataTable > .datatable--cursor {
        background: $primary 30%;
    }

    MyReviewsPane DataTable > .datatable--header {
        background: $primary;
        text-style: bold;
    }
    """

    def __init__(
        self,
        items: list[QueueItem],
        queue_db: QueueDatabase,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.items = items
        self.queue_db = queue_db
        self.selected: set[int] = set()

    def compose(self) -> ComposeResult:
        with Container(id="my-reviews-header-container"):
            yield Label("My Reviews", id="my-reviews-title")
        with Container(id="my-reviews-table-container"):
            yield DataTable(id="my-reviews-table", cursor_type="row")
        with Horizontal(id="my-reviews-status-bar"):
            yield Label("", id="my-reviews-status-label")

    def on_mount(self) -> None:
        table = self.query_one("#my-reviews-table", DataTable)
        table.add_column("", key="selected", width=3)
        table.add_column("RR#", key="rr", width=8)
        table.add_column("Diff", key="diff", width=5)
        table.add_column("Issues", key="issues", width=6)
        table.add_column("Status", key="status", width=10)
        table.add_column("Repo", key="repo", width=15)
        table.add_column("Summary", key="summary")
        self._populate_table()
        self._update_status()

    def _populate_table(self) -> None:
        table = self.query_one("#my-reviews-table", DataTable)
        table.clear()

        for item in self.items:
            icon = STATUS_ICONS.get(item.status.value, "?")
            summary = item.summary or ""

            if item.status in DIM_STATUSES:
                summary = f"[dim]{summary}[/dim]"
            elif item.status == QueueStatus.NEXT:
                summary = f"[bold]{summary}[/bold]"

            sel = "[X]" if item.review_request_id in self.selected else "[ ]"
            issues = str(item.issue_open_count) if item.issue_open_count else ""
            table.add_row(
                sel,
                str(item.review_request_id),
                str(item.diff_revision),
                issues,
                f"{icon} {item.status.value}",
                item.repository or "",
                summary,
                key=str(item.review_request_id),
            )

    def _update_status(self) -> None:
        label = self.query_one("#my-reviews-status-label", Label)
        total = len(self.items)
        selected = len(self.selected)
        label.update(f"Selected: {selected}/{total} items")

    def _toggle_row(self, rr_id: int) -> None:
        table = self.query_one("#my-reviews-table", DataTable)
        row_key = str(rr_id)

        if rr_id in self.selected:
            self.selected.remove(rr_id)
            table.update_cell(row_key, "selected", "[ ]")
        else:
            self.selected.add(rr_id)
            table.update_cell(row_key, "selected", "[X]")

        self._update_status()

    def _get_cursor_rr_id(self) -> int | None:
        table = self.query_one("#my-reviews-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            return self.items[table.cursor_row].review_request_id
        return None

    def _get_target_rr_ids(self) -> list[int]:
        """Get RR IDs to act on: selected items, or cursor item."""
        if self.selected:
            return list(self.selected)
        rr_id = self._get_cursor_rr_id()
        return [rr_id] if rr_id else []

    def _apply_status(self, new_status: QueueStatus) -> None:
        """Apply a status change to target items."""
        rr_ids = self._get_target_rr_ids()
        if not rr_ids:
            self.app.notify("No items to update", severity="warning")
            return

        updated = 0
        errors: list[str] = []
        for rr_id in rr_ids:
            try:
                self.queue_db.update_status(rr_id, new_status)
                updated += 1
            except ValueError as e:
                errors.append(str(e))

        if updated:
            self.app.notify(f"{updated} item(s) -> {new_status.value}")
        if errors:
            self.app.notify(errors[0], severity="warning")

        self.refresh_data()

    def refresh_data(self, items: list[QueueItem] | None = None) -> None:
        """Refresh table with new or re-queried items."""
        if items is not None:
            self.items = items
        elif hasattr(self.app, "refresh_my_reviews_items"):
            self.items = self.app.refresh_my_reviews_items()

        visible_ids = {item.review_request_id for item in self.items}
        self.selected &= visible_ids
        self._populate_table()
        self._update_status()

    def focus_table(self) -> None:
        """Focus the DataTable in this pane."""
        self.query_one("#my-reviews-table", DataTable).focus()

    # -- Actions --

    def action_toggle_selection(self) -> None:
        rr_id = self._get_cursor_rr_id()
        if rr_id is not None:
            self._toggle_row(rr_id)

    def action_toggle_all(self) -> None:
        table = self.query_one("#my-reviews-table", DataTable)

        if len(self.selected) == len(self.items):
            self.selected.clear()
            for item in self.items:
                table.update_cell(str(item.review_request_id), "selected", "[ ]")
        else:
            for item in self.items:
                self.selected.add(item.review_request_id)
                table.update_cell(str(item.review_request_id), "selected", "[X]")

        self._update_status()

    def action_mark_next(self) -> None:
        self._apply_status(QueueStatus.NEXT)

    def action_mark_ignore(self) -> None:
        self._apply_status(QueueStatus.IGNORE)

    def action_show_actions(self) -> None:
        """Open the action picker modal."""
        rr_ids = self._get_target_rr_ids()
        if not rr_ids:
            self.app.notify("No items selected", severity="warning")
            return

        from bb_review.ui.screens.my_reviews_action_picker import MyReviewsActionPickerScreen

        self.app.push_screen(
            MyReviewsActionPickerScreen(rr_ids=rr_ids),
            callback=self._on_action_picked,
        )

    def _on_action_picked(self, result: tuple[str, list[int]] | None) -> None:
        if result is None:
            return

        action, rr_ids = result
        action_map = {
            "next": QueueStatus.NEXT,
            "ignore": QueueStatus.IGNORE,
        }

        if action == "triage":
            if len(rr_ids) == 1:
                self.post_message(self.TriageRequested(rr_ids[0]))
            else:
                self.app.notify("Triage works on one RR at a time", severity="warning")
        elif action == "analyze":
            if len(rr_ids) == 1:
                self.post_message(self.ProcessRequested())
            else:
                self.app.notify("Analyze works on one RR at a time", severity="warning")
        elif action in action_map:
            saved = self.selected.copy()
            self.selected = set(rr_ids)
            self._apply_status(action_map[action])
            self.selected = saved

    def action_triage_item(self) -> None:
        """Launch triage on the highlighted item."""
        rr_ids = self._get_target_rr_ids()
        if not rr_ids:
            self.app.notify("No item selected", severity="warning")
            return
        if len(rr_ids) > 1:
            self.app.notify("Triage works on one RR at a time", severity="warning")
            return
        self.post_message(self.TriageRequested(rr_ids[0]))

    def action_request_sync(self) -> None:
        self.post_message(self.SyncRequested())

    def action_request_process(self) -> None:
        self.post_message(self.ProcessRequested())
