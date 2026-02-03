"""Reviews pane widget for browsing analyses within the unified TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import DataTable, Label

from bb_review.db.models import AnalysisListItem


@dataclass
class ReviewsAction:
    """Describes what the user wants to do with one or more analyses."""

    type: Literal["batch_export", "single_action", "batch_action", "open_analysis"]
    ids: list[int] | None = None
    analysis_id: int | None = None


class ReviewsPane(Container):
    """Container-based pane for browsing stored analyses (replaces AnalysisListScreen)."""

    # -- Messages posted to the parent app --

    class ActionRequested(Message):
        """User triggered an action on analyses."""

        def __init__(self, action: ReviewsAction) -> None:
            super().__init__()
            self.action = action

    # -- Bindings --

    BINDINGS = [
        Binding("space", "toggle_selection", "Toggle Select"),
        Binding("a", "toggle_all", "Select All"),
        Binding("enter", "open_analysis", "Open", priority=True),
        Binding("x", "show_actions", "Actions"),
        Binding("p", "proceed", "Export Selected"),
    ]

    DEFAULT_CSS = """
    ReviewsPane {
        height: 1fr;
    }

    ReviewsPane #reviews-header-container {
        height: auto;
        padding: 1;
        background: $surface;
    }

    ReviewsPane #reviews-title {
        text-style: bold;
        color: $text;
    }

    ReviewsPane #reviews-table-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    ReviewsPane #reviews-status-bar {
        height: auto;
        padding: 1;
        background: $surface;
        dock: bottom;
    }

    ReviewsPane DataTable {
        height: 100%;
    }

    ReviewsPane DataTable > .datatable--cursor {
        background: $primary 30%;
    }

    ReviewsPane DataTable > .datatable--header {
        background: $primary;
        text-style: bold;
    }
    """

    def __init__(self, analyses: list[AnalysisListItem], *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.analyses = analyses
        self.selected: set[int] = set()

    def compose(self) -> ComposeResult:
        with Container(id="reviews-header-container"):
            yield Label("Select Analyses", id="reviews-title")
        with Container(id="reviews-table-container"):
            yield DataTable(id="reviews-table", cursor_type="row")
        with Horizontal(id="reviews-status-bar"):
            yield Label("", id="reviews-status-label")

    def on_mount(self) -> None:
        table = self.query_one("#reviews-table", DataTable)
        table.add_column("", key="selected", width=3)
        table.add_column("ID", key="id", width=6)
        table.add_column("RR#", key="rr", width=8)
        table.add_column("Repo", key="repo", width=15)
        table.add_column("Issues", key="issues", width=8)
        table.add_column("Status", key="status", width=12)
        table.add_column("Summary", key="summary")
        self._populate_table()
        self._update_status()

    def _get_status_icon(self, status: str) -> str:
        icons = {
            "draft": "D",
            "submitted": "S",
            "obsolete": "O",
            "invalid": "I",
        }
        return icons.get(status, "?")

    def _populate_table(self) -> None:
        table = self.query_one("#reviews-table", DataTable)
        table.clear()

        for analysis in self.analyses:
            status_icon = self._get_status_icon(analysis.status.value)
            rr_summary = analysis.rr_summary or ""
            summary = rr_summary[:50]
            if len(rr_summary) > 50:
                summary += "..."

            if analysis.fake:
                summary = f"[FAKE] {summary}"

            sel = "[X]" if analysis.id in self.selected else "[ ]"
            table.add_row(
                sel,
                str(analysis.id),
                str(analysis.review_request_id),
                analysis.repository,
                str(analysis.issue_count),
                f"{status_icon} {analysis.status.value}",
                summary,
                key=str(analysis.id),
            )

    def _update_status(self) -> None:
        label = self.query_one("#reviews-status-label", Label)
        total = len(self.analyses)
        selected = len(self.selected)
        label.update(f"Selected: {selected}/{total} analyses")

    def _toggle_row(self, row_key: str) -> None:
        analysis_id = int(row_key)
        table = self.query_one("#reviews-table", DataTable)

        if analysis_id in self.selected:
            self.selected.remove(analysis_id)
            table.update_cell(row_key, "selected", "[ ]")
        else:
            self.selected.add(analysis_id)
            table.update_cell(row_key, "selected", "[X]")

        self._update_status()

    def refresh_data(self, analyses: list[AnalysisListItem] | None = None) -> None:
        """Refresh table with new or re-queried analyses."""
        if analyses is not None:
            self.analyses = analyses
        elif hasattr(self.app, "refresh_review_items"):
            self.analyses = self.app.refresh_review_items()

        visible_ids = {a.id for a in self.analyses}
        self.selected &= visible_ids
        self._populate_table()
        self._update_status()

    def focus_table(self) -> None:
        """Focus the DataTable in this pane."""
        self.query_one("#reviews-table", DataTable).focus()

    # -- Actions --

    def action_toggle_selection(self) -> None:
        table = self.query_one("#reviews-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            row_key_str = str(self.analyses[table.cursor_row].id)
            self._toggle_row(row_key_str)

    def action_toggle_all(self) -> None:
        table = self.query_one("#reviews-table", DataTable)

        if len(self.selected) == len(self.analyses):
            self.selected.clear()
            for analysis in self.analyses:
                table.update_cell(str(analysis.id), "selected", "[ ]")
        else:
            for analysis in self.analyses:
                self.selected.add(analysis.id)
                table.update_cell(str(analysis.id), "selected", "[X]")

        self._update_status()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row double-click - same as Enter."""
        self.action_open_analysis()

    def action_open_analysis(self) -> None:
        table = self.query_one("#reviews-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            analysis_id = self.analyses[table.cursor_row].id
            self.post_message(
                self.ActionRequested(ReviewsAction(type="open_analysis", analysis_id=analysis_id))
            )

    def action_show_actions(self) -> None:
        if self.selected:
            self.post_message(
                self.ActionRequested(ReviewsAction(type="batch_action", ids=list(self.selected)))
            )
        else:
            table = self.query_one("#reviews-table", DataTable)
            if table.cursor_row is not None and table.row_count > 0:
                analysis_id = self.analyses[table.cursor_row].id
                self.post_message(
                    self.ActionRequested(ReviewsAction(type="single_action", analysis_id=analysis_id))
                )

    def action_proceed(self) -> None:
        if not self.selected:
            self.app.notify("No analyses selected. Press Space to select.", severity="warning")
            return
        self.post_message(self.ActionRequested(ReviewsAction(type="batch_export", ids=list(self.selected))))
