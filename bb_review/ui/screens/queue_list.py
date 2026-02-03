"""Queue list screen for triaging queue items."""

from dataclasses import dataclass, field
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

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

# Statuses that appear dimmed
DIM_STATUSES = {QueueStatus.IGNORE, QueueStatus.DONE}


@dataclass
class QueueListResult:
    """Result from the queue list screen."""

    type: Literal["set_status", "delete", "quit"]
    rr_ids: list[int] = field(default_factory=list)
    status: QueueStatus | None = None


class QueueListScreen(Screen):
    """Screen for listing and triaging queue items."""

    BINDINGS = [
        Binding("space", "toggle_selection", "Toggle Select"),
        Binding("a", "toggle_all", "Select All"),
        Binding("n", "mark_next", "Next"),
        Binding("i", "mark_ignore", "Ignore"),
        Binding("f", "mark_finished", "Done"),
        Binding("d", "delete_item", "Delete"),
        Binding("x", "show_actions", "Actions"),
        Binding("q", "quit_app", "Quit"),
        Binding("escape", "quit_app", "Quit", show=False),
    ]

    CSS = """
    QueueListScreen {
        layout: vertical;
    }

    #header-container {
        height: auto;
        padding: 1;
        background: $surface;
    }

    #title {
        text-style: bold;
        color: $text;
    }

    #table-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    #status-bar {
        height: auto;
        padding: 1;
        background: $surface;
        dock: bottom;
    }

    DataTable {
        height: 100%;
    }

    DataTable > .datatable--cursor {
        background: $primary 30%;
    }

    DataTable > .datatable--header {
        background: $primary;
        text-style: bold;
    }
    """

    def __init__(
        self,
        items: list[QueueItem],
        queue_db: QueueDatabase,
        name: str | None = None,
    ):
        super().__init__(name=name)
        self.items = items
        self.queue_db = queue_db
        self.selected: set[int] = set()  # Set of selected review_request_ids

    def compose(self) -> ComposeResult:
        yield Header()

        with Vertical():
            with Container(id="header-container"):
                yield Label("Review Queue", id="title")

            with Container(id="table-container"):
                yield DataTable(id="queue-table", cursor_type="row")

            with Horizontal(id="status-bar"):
                yield Label("", id="status-label")

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)

        table.add_column("", key="selected", width=3)
        table.add_column("RR#", key="rr", width=8)
        table.add_column("Diff", key="diff", width=5)
        table.add_column("Status", key="status", width=10)
        table.add_column("Repo", key="repo", width=15)
        table.add_column("Submitter", key="submitter", width=12)
        table.add_column("Summary", key="summary")

        self._populate_table()
        self._update_status()

    def _populate_table(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear()

        for item in self.items:
            icon = STATUS_ICONS.get(item.status.value, "?")
            summary = item.summary or ""

            # Dim styling for ignore/done
            if item.status in DIM_STATUSES:
                summary = f"[dim]{summary}[/dim]"
            elif item.status == QueueStatus.NEXT:
                summary = f"[bold]{summary}[/bold]"

            sel = "[X]" if item.review_request_id in self.selected else "[ ]"
            table.add_row(
                sel,
                str(item.review_request_id),
                str(item.diff_revision),
                f"{icon} {item.status.value}",
                item.repository or "",
                item.submitter or "",
                summary,
                key=str(item.review_request_id),
            )

    def _update_status(self) -> None:
        label = self.query_one("#status-label", Label)
        total = len(self.items)
        selected = len(self.selected)
        label.update(f"Selected: {selected}/{total} items")

    def _toggle_row(self, rr_id: int) -> None:
        table = self.query_one("#queue-table", DataTable)
        row_key = str(rr_id)

        if rr_id in self.selected:
            self.selected.remove(rr_id)
            table.update_cell(row_key, "selected", "[ ]")
        else:
            self.selected.add(rr_id)
            table.update_cell(row_key, "selected", "[X]")

        self._update_status()

    def _get_cursor_rr_id(self) -> int | None:
        table = self.query_one("#queue-table", DataTable)
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
            self.notify("No items to update", severity="warning")
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
            self.notify(f"{updated} item(s) -> {new_status.value}")
        if errors:
            # Show first error as notification
            self.notify(errors[0], severity="warning")

        self._refresh()

    def _refresh(self) -> None:
        """Re-query items and repopulate table."""
        # Ask the app to refresh items (it holds the filter params)
        if hasattr(self.app, "refresh_items"):
            self.items = self.app.refresh_items()
        self._populate_table()
        self._update_status()

    # -- Actions --

    def action_toggle_selection(self) -> None:
        rr_id = self._get_cursor_rr_id()
        if rr_id is not None:
            self._toggle_row(rr_id)

    def action_toggle_all(self) -> None:
        table = self.query_one("#queue-table", DataTable)

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

    def action_mark_finished(self) -> None:
        self._apply_status(QueueStatus.DONE)

    def action_delete_item(self) -> None:
        rr_ids = self._get_target_rr_ids()
        if not rr_ids:
            self.notify("No items to delete", severity="warning")
            return

        deleted = 0
        for rr_id in rr_ids:
            if self.queue_db.delete_item(rr_id):
                self.selected.discard(rr_id)
                deleted += 1

        if deleted:
            self.notify(f"Deleted {deleted} item(s)")

        self._refresh()

    def action_show_actions(self) -> None:
        """Open the action picker modal."""
        rr_ids = self._get_target_rr_ids()
        if not rr_ids:
            self.notify("No items selected", severity="warning")
            return

        from .queue_action_picker import QueueActionPickerScreen

        self.app.push_screen(
            QueueActionPickerScreen(rr_ids=rr_ids),
            callback=self._on_action_picked,
        )

    def _on_action_picked(self, result: tuple[str, list[int]] | None) -> None:
        if result is None:
            return

        action, rr_ids = result
        action_map = {
            "next": QueueStatus.NEXT,
            "ignore": QueueStatus.IGNORE,
            "done": QueueStatus.DONE,
        }

        if action == "delete":
            deleted = 0
            for rr_id in rr_ids:
                if self.queue_db.delete_item(rr_id):
                    self.selected.discard(rr_id)
                    deleted += 1
            if deleted:
                self.notify(f"Deleted {deleted} item(s)")
            self._refresh()
        elif action in action_map:
            # Temporarily set target rr_ids for _apply_status
            saved = self.selected.copy()
            self.selected = set(rr_ids)
            self._apply_status(action_map[action])
            self.selected = saved

    def action_quit_app(self) -> None:
        self.app.exit()
