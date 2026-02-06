"""Work pane widget for triage sessions in the unified TUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import DataTable, Label


@dataclass
class WorkItem:
    """A triage/work session entry for display."""

    rr_id: int
    repository: str
    plan_path: str
    fix_count: int
    reply_count: int
    skip_count: int
    status: str  # pending, triaged, planned, done

    @property
    def total(self) -> int:
        return self.fix_count + self.reply_count + self.skip_count


class WorkPane(Container):
    """Container pane for listing triage sessions / work items."""

    class TriageRequested(Message):
        """User wants to launch triage on a review request."""

        def __init__(self, rr_id: int | None = None) -> None:
            super().__init__()
            self.rr_id = rr_id

    BINDINGS = [
        Binding("t", "launch_triage", "Triage"),
        Binding("enter", "open_plan", "Open Plan"),
        Binding("r", "refresh", "Refresh"),
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

    def __init__(self, work_items: list[WorkItem] | None = None, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.work_items = work_items or []

    def compose(self) -> ComposeResult:
        with Container(id="work-header-container"):
            yield Label("Work Items (Triage Sessions)", id="work-title")
        with Container(id="work-table-container"):
            yield DataTable(id="work-table", cursor_type="row")
        with Horizontal(id="work-status-bar"):
            yield Label("", id="work-status-label")

    def on_mount(self) -> None:
        table = self.query_one("#work-table", DataTable)
        table.add_column("RR#", key="rr", width=8)
        table.add_column("Repo", key="repo", width=15)
        table.add_column("Fixes", key="fixes", width=7)
        table.add_column("Replies", key="replies", width=8)
        table.add_column("Skipped", key="skipped", width=8)
        table.add_column("Status", key="status", width=10)
        table.add_column("Plan", key="plan")
        self._populate_table()
        self._update_status()

    def _populate_table(self) -> None:
        table = self.query_one("#work-table", DataTable)
        table.clear()
        for i, item in enumerate(self.work_items):
            table.add_row(
                str(item.rr_id),
                item.repository,
                str(item.fix_count),
                str(item.reply_count),
                str(item.skip_count),
                item.status,
                item.plan_path,
                key=str(i),
            )

    def _update_status(self) -> None:
        label = self.query_one("#work-status-label", Label)
        label.update(f"{len(self.work_items)} work items  |  t=triage Enter=open r=refresh")

    def refresh_data(self, items: list[WorkItem] | None = None) -> None:
        if items is not None:
            self.work_items = items
        self._populate_table()
        self._update_status()

    def focus_table(self) -> None:
        self.query_one("#work-table", DataTable).focus()

    def action_launch_triage(self) -> None:
        self.post_message(self.TriageRequested())

    def action_open_plan(self) -> None:
        table = self.query_one("#work-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self.work_items):
            item = self.work_items[table.cursor_row]
            self.app.notify(f"Plan: {item.plan_path}", severity="information")

    def action_refresh(self) -> None:
        self._scan_plan_files()

    def _scan_plan_files(self) -> None:
        """Scan current directory for triage_*.yaml plan files."""
        from bb_review.triage.plan_writer import read_fix_plan

        items: list[WorkItem] = []
        for path in sorted(Path(".").glob("triage_*.yaml")):
            try:
                plan = read_fix_plan(path)
                items.append(
                    WorkItem(
                        rr_id=plan.review_request_id,
                        repository=plan.repository,
                        plan_path=str(path),
                        fix_count=plan.fix_count,
                        reply_count=plan.reply_count,
                        skip_count=plan.skip_count,
                        status="planned",
                    )
                )
            except Exception:
                pass

        self.refresh_data(items)
