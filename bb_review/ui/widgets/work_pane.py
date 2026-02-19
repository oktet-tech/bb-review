"""Work pane widget for DB-backed triage sessions in the unified TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import DataTable, Label


if TYPE_CHECKING:
    from bb_review.db.models import TriageListItem


@dataclass
class WorkAction:
    """Describes what the user wants to do with one or more triage sessions."""

    type: Literal["open", "single_action", "batch_action", "batch_delete"]
    ids: list[int] | None = None
    triage_id: int | None = None


class WorkPane(Container):
    """Container pane for listing triage sessions from the database."""

    class OpenRequested(Message):
        """User wants to open a triage session."""

        def __init__(self, triage_id: int) -> None:
            super().__init__()
            self.triage_id = triage_id

    class ActionRequested(Message):
        """User triggered an action on triage sessions."""

        def __init__(self, action: WorkAction) -> None:
            super().__init__()
            self.action = action

    class TriageRequested(Message):
        """User wants to launch triage on a review request."""

        def __init__(self, rr_id: int | None = None) -> None:
            super().__init__()
            self.rr_id = rr_id

    BINDINGS = [
        Binding("enter", "open_triage", "Open", priority=True),
        Binding("x", "show_actions", "Actions"),
        Binding("space", "toggle_selection", "Toggle Select"),
        Binding("a", "toggle_all", "Select All"),
        Binding("r", "refresh", "Refresh"),
        Binding("d", "delete", "Delete"),
        Binding("t", "launch_triage", "Triage"),
    ]

    DEFAULT_CSS = """
    WorkPane {
        height: 1fr;
    }

    WorkPane #work-header-container {
        height: auto;
        padding: 1;
        background: $surface;
    }

    WorkPane #work-title {
        text-style: bold;
        color: $text;
    }

    WorkPane #work-table-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    WorkPane DataTable {
        height: 100%;
    }

    WorkPane DataTable > .datatable--cursor {
        background: $primary 30%;
    }

    WorkPane DataTable > .datatable--header {
        background: $primary;
        text-style: bold;
    }

    WorkPane #work-status-bar {
        height: auto;
        padding: 1;
        background: $surface;
        dock: bottom;
    }
    """

    def __init__(
        self,
        items: list[TriageListItem] | None = None,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.items: list[TriageListItem] = items or []
        self.selected: set[int] = set()

    def compose(self) -> ComposeResult:
        with Container(id="work-header-container"):
            yield Label("Work Items (Triage Sessions)", id="work-title")
        with Container(id="work-table-container"):
            yield DataTable(id="work-table", cursor_type="row")
        with Horizontal(id="work-status-bar"):
            yield Label("", id="work-status-label")

    def on_mount(self) -> None:
        table = self.query_one("#work-table", DataTable)
        table.add_column("", key="selected", width=3)
        table.add_column("ID", key="id", width=6)
        table.add_column("RR#", key="rr", width=8)
        table.add_column("Repo", key="repo", width=15)
        table.add_column("Fixes", key="fixes", width=7)
        table.add_column("Replies", key="replies", width=8)
        table.add_column("Skip", key="skip", width=7)
        table.add_column("Status", key="status", width=10)
        table.add_column("Summary", key="summary")
        self._populate_table()
        self._update_status()

    def _populate_table(self) -> None:
        table = self.query_one("#work-table", DataTable)
        table.clear()
        for item in self.items:
            sel = "[X]" if item.id in self.selected else "[ ]"
            summary = (item.summary or "")[:50]
            if len(item.summary or "") > 50:
                summary += "..."
            table.add_row(
                sel,
                str(item.id),
                str(item.review_request_id),
                item.repository,
                str(item.fix_count),
                str(item.reply_count),
                str(item.skip_count),
                item.status.value,
                summary,
                key=str(item.id),
            )

    def _update_status(self) -> None:
        label = self.query_one("#work-status-label", Label)
        total = len(self.items)
        selected = len(self.selected)
        label.update(
            f"{selected}/{total} selected  |  "
            "Enter=open x=actions Space=select a=all r=refresh d=delete t=triage"
        )

    def refresh_data(self, items: list[TriageListItem] | None = None) -> None:
        if items is not None:
            self.items = items
        visible_ids = {i.id for i in self.items}
        self.selected &= visible_ids
        self._populate_table()
        self._update_status()

    def focus_table(self) -> None:
        self.query_one("#work-table", DataTable).focus()

    def _current_item(self) -> TriageListItem | None:
        table = self.query_one("#work-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self.items):
            return self.items[table.cursor_row]
        return None

    # -- Actions --

    def action_open_triage(self) -> None:
        item = self._current_item()
        if item:
            self.post_message(self.OpenRequested(item.id))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_triage()

    def action_show_actions(self) -> None:
        if self.selected:
            self.post_message(self.ActionRequested(WorkAction(type="batch_action", ids=list(self.selected))))
        else:
            item = self._current_item()
            if item:
                self.post_message(self.ActionRequested(WorkAction(type="single_action", triage_id=item.id)))

    def action_toggle_selection(self) -> None:
        item = self._current_item()
        if item:
            self._toggle_row(item.id)

    def action_toggle_all(self) -> None:
        table = self.query_one("#work-table", DataTable)
        if len(self.selected) == len(self.items):
            self.selected.clear()
            for item in self.items:
                table.update_cell(str(item.id), "selected", "[ ]")
        else:
            for item in self.items:
                self.selected.add(item.id)
                table.update_cell(str(item.id), "selected", "[X]")
        self._update_status()

    def action_refresh(self) -> None:
        if hasattr(self.app, "refresh_work_pane"):
            self.app.refresh_work_pane()

    def action_delete(self) -> None:
        ids = list(self.selected) if self.selected else []
        if not ids:
            item = self._current_item()
            if item:
                ids = [item.id]
        if ids:
            self.post_message(self.ActionRequested(WorkAction(type="batch_delete", ids=ids)))

    def action_launch_triage(self) -> None:
        self.post_message(self.TriageRequested())

    def _toggle_row(self, triage_id: int) -> None:
        table = self.query_one("#work-table", DataTable)
        key = str(triage_id)
        if triage_id in self.selected:
            self.selected.remove(triage_id)
            table.update_cell(key, "selected", "[ ]")
        else:
            self.selected.add(triage_id)
            table.update_cell(key, "selected", "[X]")
        self._update_status()
