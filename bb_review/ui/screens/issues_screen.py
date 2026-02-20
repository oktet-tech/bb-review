"""Issues viewer screen for My Reviews -- shows open issues on an RR."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from bb_review.triage.models import RBComment
from bb_review.ui.utils import extract_file_diff
from bb_review.ui.widgets.diff_viewer import DiffViewer


class IssuesScreen(Screen):
    """Pushed screen showing open issues for a single review request."""

    BINDINGS = [
        Binding("v", "toggle_diff", "Diff"),
        Binding("b", "go_back", "Back"),
        Binding("escape", "go_back", "Back", show=False),
    ]

    CSS = """
    #issues-header {
        height: auto;
        padding: 1;
        background: $surface;
    }

    #issues-title {
        text-style: bold;
    }

    #issues-table-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    #issues-table-container DataTable {
        height: 100%;
    }

    #issues-table-container DataTable > .datatable--cursor {
        background: $primary 30%;
    }

    #issues-table-container DataTable > .datatable--header {
        background: $primary;
        text-style: bold;
    }

    #issues-detail {
        height: 12;
        border: solid $accent;
        margin: 0 1;
        padding: 1;
        overflow-y: auto;
    }

    #issues-status {
        height: auto;
        padding: 1;
        background: $surface;
        dock: bottom;
    }
    """

    def __init__(
        self,
        comments: list[RBComment],
        raw_diff: str,
        rr_id: int,
        *,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._comments = comments
        self._raw_diff = raw_diff
        self._rr_id = rr_id
        self._diff_visible = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="issues-header"):
            yield Label(
                f"Open Issues - r/{self._rr_id} ({len(self._comments)} issues)",
                id="issues-title",
            )
        with Container(id="issues-table-container"):
            yield DataTable(id="issues-table", cursor_type="row")
        yield Static("", id="issues-detail")
        yield DiffViewer(id="issues-diff-viewer")
        with Horizontal(id="issues-status"):
            yield Label(
                "v=diff  b/Esc=back",
                id="issues-status-label",
            )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#issues-table", DataTable)
        table.add_column("Reviewer", key="reviewer", width=14)
        table.add_column("Location", key="location", width=30)
        table.add_column("Status", key="status", width=10)
        table.add_column("Comment", key="comment")
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#issues-table", DataTable)
        table.clear()

        for i, c in enumerate(self._comments):
            location = f"{c.file_path}:{c.line_number}" if c.file_path else "body"
            status = c.issue_status or "open"
            text = c.text[:80].replace("\n", " ")
            if len(c.text) > 80:
                text += "..."

            table.add_row(c.reviewer, location, status, text, key=str(i))

    def _update_detail(self) -> None:
        table = self.query_one("#issues-table", DataTable)
        panel = self.query_one("#issues-detail", Static)
        if table.cursor_row is None or table.cursor_row >= len(self._comments):
            panel.update("")
            return

        c = self._comments[table.cursor_row]
        lines = [f"Reviewer: {c.reviewer}"]
        if c.file_path:
            lines.append(f"Location: {c.file_path}:{c.line_number}")
        lines.append(f"Status: {c.issue_status or 'open'}")
        lines.append(f"\n{c.text}")
        panel.update("\n".join(lines))

    def _update_diff_viewer(self) -> None:
        viewer = self.query_one("#issues-diff-viewer", DiffViewer)
        table = self.query_one("#issues-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._comments):
            viewer.update_content(None)
            return

        c = self._comments[table.cursor_row]
        if c.file_path and self._raw_diff:
            file_diff = extract_file_diff(self._raw_diff, c.file_path)
            viewer.update_content(file_diff, c.file_path, c.line_number)
        else:
            viewer.update_content(None)

    def on_data_table_cursor_moved(self, event: DataTable.CursorMoved) -> None:
        self._update_detail()
        if self._diff_visible:
            self._update_diff_viewer()

    def action_toggle_diff(self) -> None:
        viewer = self.query_one("#issues-diff-viewer", DiffViewer)
        viewer.toggle()
        self._diff_visible = viewer.is_visible
        self.query_one("#issues-detail", Static).display = not self._diff_visible
        if self._diff_visible:
            self._update_diff_viewer()

    def action_go_back(self) -> None:
        self.dismiss(None)
