"""Analysis list screen for selecting analyses."""

from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from bb_review.db.models import AnalysisListItem


@dataclass
class AnalysisListResult:
    """Result from the analysis list screen."""

    type: Literal["batch_export", "single_action"]
    ids: list[int] | None = None  # For batch_export
    analysis_id: int | None = None  # For single_action


class AnalysisListScreen(Screen):
    """Screen for listing and selecting analyses."""

    BINDINGS = [
        Binding("space", "toggle_selection", "Toggle Select"),
        Binding("a", "toggle_all", "Select All"),
        Binding("enter", "show_actions", "Actions", priority=True),
        Binding("p", "proceed", "Export Selected"),
        Binding("q", "quit_app", "Quit"),
        Binding("escape", "quit_app", "Quit"),
    ]

    CSS = """
    AnalysisListScreen {
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

    #instructions {
        color: $text-muted;
        margin-top: 1;
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

    def __init__(self, analyses: list[AnalysisListItem], name: str | None = None):
        """Initialize the analysis list screen.

        Args:
            analyses: List of analyses to display
            name: Optional screen name
        """
        super().__init__(name=name)
        self.analyses = analyses
        self.selected: set[int] = set()  # Set of selected analysis IDs

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            with Container(id="header-container"):
                yield Label("Select Analyses", id="title")
                yield Static(
                    "[Space] Toggle  [A] All  [Enter] Actions  [P] Export Selected  [Q] Quit",
                    id="instructions",
                )

            with Container(id="table-container"):
                yield DataTable(id="analysis-table", cursor_type="row")

            with Horizontal(id="status-bar"):
                yield Label("", id="status-label")

        yield Footer()

    def on_mount(self) -> None:
        """Set up the table when screen is mounted."""
        table = self.query_one("#analysis-table", DataTable)

        # Add columns
        table.add_column("", key="selected", width=3)
        table.add_column("ID", key="id", width=6)
        table.add_column("RR#", key="rr", width=8)
        table.add_column("Repo", key="repo", width=15)
        table.add_column("Issues", key="issues", width=8)
        table.add_column("Status", key="status", width=10)
        table.add_column("Summary", key="summary")

        # Add rows
        for analysis in self.analyses:
            status_icon = self._get_status_icon(analysis.status.value)
            # Show RR summary (commit description), not the AI analysis summary
            rr_summary = analysis.rr_summary or ""
            summary = rr_summary[:50]
            if len(rr_summary) > 50:
                summary += "..."

            table.add_row(
                "[ ]",
                str(analysis.id),
                str(analysis.review_request_id),
                analysis.repository,
                str(analysis.issue_count),
                f"{status_icon} {analysis.status.value}",
                summary,
                key=str(analysis.id),
            )

        self._update_status()

    def _get_status_icon(self, status: str) -> str:
        """Get icon for status."""
        icons = {
            "draft": "D",
            "submitted": "S",
            "obsolete": "O",
            "invalid": "I",
        }
        return icons.get(status, "?")

    def _update_status(self) -> None:
        """Update the status bar."""
        label = self.query_one("#status-label", Label)
        total = len(self.analyses)
        selected = len(self.selected)
        label.update(f"Selected: {selected}/{total} analyses")

    def _toggle_row(self, row_key: str) -> None:
        """Toggle selection for a row."""
        analysis_id = int(row_key)
        table = self.query_one("#analysis-table", DataTable)

        if analysis_id in self.selected:
            self.selected.remove(analysis_id)
            table.update_cell(row_key, "selected", "[ ]")
        else:
            self.selected.add(analysis_id)
            table.update_cell(row_key, "selected", "[X]")

        self._update_status()

    def action_toggle_selection(self) -> None:
        """Toggle selection on current row."""
        table = self.query_one("#analysis-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            row_key_str = str(self.analyses[table.cursor_row].id)
            self._toggle_row(row_key_str)

    def action_toggle_all(self) -> None:
        """Toggle all selections."""
        table = self.query_one("#analysis-table", DataTable)

        if len(self.selected) == len(self.analyses):
            # Deselect all
            self.selected.clear()
            for analysis in self.analyses:
                table.update_cell(str(analysis.id), "selected", "[ ]")
        else:
            # Select all
            for analysis in self.analyses:
                self.selected.add(analysis.id)
                table.update_cell(str(analysis.id), "selected", "[X]")

        self._update_status()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row double-click on DataTable - show actions."""
        # Note: Enter key is handled by action_show_actions binding
        # This handles double-click
        if event.row_key:
            analysis_id = int(event.row_key.value)
            self.dismiss(AnalysisListResult(type="single_action", analysis_id=analysis_id))

    def action_show_actions(self) -> None:
        """Show action picker for current row."""
        table = self.query_one("#analysis-table", DataTable)
        if table.cursor_row is not None and table.row_count > 0:
            analysis_id = self.analyses[table.cursor_row].id
            self.dismiss(AnalysisListResult(type="single_action", analysis_id=analysis_id))

    def action_proceed(self) -> None:
        """Proceed with batch export of selected analyses."""
        if not self.selected:
            self.notify("No analyses selected. Press Space to select.", severity="warning")
            return

        # Return batch export result
        self.dismiss(AnalysisListResult(type="batch_export", ids=list(self.selected)))

    def action_quit_app(self) -> None:
        """Quit the application."""
        self.app.exit()
