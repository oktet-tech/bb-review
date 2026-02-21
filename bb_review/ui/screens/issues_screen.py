"""Issues viewer screen for My Reviews -- shows open issues on an RR."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from bb_review.triage.models import RBComment
from bb_review.ui.utils import extract_file_diff
from bb_review.ui.widgets.diff_viewer import DiffViewer


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Comment view modal
# ---------------------------------------------------------------------------


class CommentViewModal(ModalScreen[None]):
    """Scrollable read-only view of a single comment."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
    ]

    CSS = """
    CommentViewModal {
        align: center middle;
    }

    #comment-dialog {
        width: 80;
        height: 24;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #comment-title {
        text-style: bold;
        text-align: center;
        width: 100%;
        padding-bottom: 1;
    }

    #comment-area {
        height: 1fr;
    }
    """

    def __init__(self, comment: RBComment) -> None:
        super().__init__()
        self._comment = comment

    def compose(self) -> ComposeResult:
        c = self._comment
        location = f"{c.file_path}:{c.line_number}" if c.file_path else "general"
        title = f"{c.reviewer} -- {location} ({c.issue_status or 'open'})"

        with Container(id="comment-dialog"):
            yield Label(title, id="comment-title")
            yield TextArea(c.text, read_only=True, id="comment-area")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#comment-area", TextArea).focus()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Export modal
# ---------------------------------------------------------------------------


class IssuesExportModal(ModalScreen[str | None]):
    """Picker for exporting open issues."""

    BINDINGS = [
        Binding("s", "pick_single", "Single file", show=False),
        Binding("e", "pick_each", "Each file", show=False),
        Binding("c", "pick_clipboard", "Clipboard", show=False),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    IssuesExportModal {
        align: center middle;
    }

    #export-dialog {
        width: 55;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #export-title {
        text-style: bold;
        text-align: center;
        width: 100%;
        padding-bottom: 1;
    }

    OptionList {
        height: auto;
        max-height: 10;
        background: $surface;
    }

    OptionList:focus {
        border: tall $primary;
    }
    """

    def __init__(self, comments: list[RBComment], rr_id: int) -> None:
        super().__init__()
        self._comments = comments
        self._rr_id = rr_id

    def compose(self) -> ComposeResult:
        with Container(id="export-dialog"):
            yield Label("Export Issues", id="export-title")
            yield OptionList(
                Option("\\[S] Single file -- all issues in one Markdown", id="single"),
                Option("\\[E] Each issue separate -- one file per issue", id="each"),
                Option("\\[C] Clipboard -- copy Markdown to clipboard", id="clipboard"),
                None,
                Option("\\[Esc] Cancel", id="cancel"),
                id="export-list",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#export-list", OptionList).focus()

    # -- selection helpers --

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select(str(event.option_id))

    def action_select(self) -> None:
        ol = self.query_one("#export-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id:
                self._select(str(opt.id))

    def _select(self, option_id: str) -> None:
        dispatch = {
            "single": self._export_single,
            "each": self._export_each,
            "clipboard": self._export_clipboard,
        }
        fn = dispatch.get(option_id)
        if fn:
            self.dismiss(fn())
        elif option_id == "cancel":
            self.dismiss(None)

    # -- formatters --

    def _comment_markdown(self, c: RBComment) -> str:
        location = f"{c.file_path}:{c.line_number}" if c.file_path else "general"
        status = c.issue_status or "open"
        return f"## {location}\nReviewer: {c.reviewer}\nStatus: {status}\n\n{c.text}"

    def _full_markdown(self) -> str:
        header = f"# Open Issues - r/{self._rr_id} ({len(self._comments)})\n\n"
        body = "\n\n".join(self._comment_markdown(c) for c in self._comments)
        return header + body

    # -- export actions --

    def _export_single(self) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(f"issues_{self._rr_id}_{ts}.md")
        path.write_text(self._full_markdown())
        return f"Exported to {path}"

    def _export_each(self) -> str:
        files: list[str] = []
        for c in self._comments:
            path = Path(f"issue_{self._rr_id}_{c.comment_id}.md")
            path.write_text(self._comment_markdown(c))
            files.append(str(path))
        return f"Exported {len(files)} file(s): {', '.join(files)}"

    def _export_clipboard(self) -> str:
        text = self._full_markdown()
        try:
            subprocess.run(
                ["pbcopy"],
                input=text.encode(),
                check=True,
                capture_output=True,
            )
            return f"Copied {len(self._comments)} issue(s) to clipboard"
        except Exception as e:
            logger.warning("Clipboard copy failed: %s", e)
            return f"Clipboard copy failed: {e}"

    # -- shortcut actions --

    def action_pick_single(self) -> None:
        self.dismiss(self._export_single())

    def action_pick_each(self) -> None:
        self.dismiss(self._export_each())

    def action_pick_clipboard(self) -> None:
        self.dismiss(self._export_clipboard())

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------


class IssuesScreen(Screen):
    """Pushed screen showing open issues for a single review request."""

    BINDINGS = [
        Binding("enter", "view_comment", "View", priority=True),
        Binding("v", "toggle_diff", "Diff"),
        Binding("e", "export", "Export"),
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
                "enter=view  v=diff  e=export  b/Esc=back",
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
        self._update_detail()

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
            if file_diff is None:
                # Log for diagnostics -- helps track prefix mismatches
                diff_files = [ln for ln in self._raw_diff.split("\n") if ln.startswith("diff --git ")]
                logger.debug(
                    "No diff found for %s; diff has %d file sections: %s",
                    c.file_path,
                    len(diff_files),
                    diff_files[:5],
                )
            viewer.update_content(file_diff, c.file_path, c.line_number)
        else:
            viewer.update_content(None)

    def on_data_table_cursor_moved(self, event: DataTable.CursorMoved) -> None:
        self._update_detail()
        if self._diff_visible:
            self._update_diff_viewer()

    # -- actions --

    def action_view_comment(self) -> None:
        table = self.query_one("#issues-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._comments):
            return
        self.app.push_screen(CommentViewModal(self._comments[table.cursor_row]))

    def action_toggle_diff(self) -> None:
        viewer = self.query_one("#issues-diff-viewer", DiffViewer)
        viewer.toggle()
        self._diff_visible = viewer.is_visible
        self.query_one("#issues-detail", Static).display = not self._diff_visible
        if self._diff_visible:
            self._update_diff_viewer()

    def action_export(self) -> None:
        self.app.push_screen(
            IssuesExportModal(self._comments, self._rr_id),
            self._on_export_done,
        )

    def _on_export_done(self, result: str | None) -> None:
        if result:
            self.app.notify(result, severity="information")

    def action_go_back(self) -> None:
        self.dismiss(None)
