"""Interactive triage screen for classifying and acting on review comments."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
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

from bb_review.triage.models import (
    CommentClassification,
    SelectableTriagedComment,
    TriageAction,
)


# Display helpers

ACTION_LABELS = {
    TriageAction.FIX: ("[FIX]", "green"),
    TriageAction.REPLY: ("[RPL]", "cyan"),
    TriageAction.SKIP: ("[SKP]", "dim"),
    TriageAction.DISAGREE: ("[DIS]", "yellow"),
}

CLASS_LABELS = {
    CommentClassification.VALID: "valid",
    CommentClassification.CONFUSED: "confused",
    CommentClassification.NITPICK: "nitpick",
    CommentClassification.OUTDATED: "outdated",
    CommentClassification.ALREADY_FIXED: "already_fixed",
    CommentClassification.DUPLICATE: "duplicate",
}


class TriageScreen(Container):
    """Main triage pane showing comments and their actions."""

    BINDINGS = [
        Binding("f", "set_fix", "Fix"),
        Binding("r", "set_reply", "Reply"),
        Binding("s", "set_skip", "Skip"),
        Binding("d", "set_disagree", "Disagree"),
        Binding("e", "edit_reply", "Edit Reply"),
        Binding("D", "done", "Done"),
    ]

    DEFAULT_CSS = """
    TriageScreen {
        height: 1fr;
    }

    TriageScreen #triage-header {
        height: auto;
        padding: 1;
        background: $surface;
    }

    TriageScreen #triage-title {
        text-style: bold;
    }

    TriageScreen #triage-table-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    TriageScreen DataTable {
        height: 100%;
    }

    TriageScreen DataTable > .datatable--cursor {
        background: $primary 30%;
    }

    TriageScreen DataTable > .datatable--header {
        background: $primary;
        text-style: bold;
    }

    TriageScreen #detail-panel {
        height: 12;
        border: solid $accent;
        margin: 0 1;
        padding: 1;
        overflow-y: auto;
    }

    TriageScreen #triage-status {
        height: auto;
        padding: 1;
        background: $surface;
        dock: bottom;
    }
    """

    def __init__(
        self,
        selectables: list[SelectableTriagedComment],
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.selectables = selectables

    def compose(self) -> ComposeResult:
        with Container(id="triage-header"):
            yield Label("Triage Review Comments", id="triage-title")
        with Container(id="triage-table-container"):
            yield DataTable(id="triage-table", cursor_type="row")
        yield Static("", id="detail-panel")
        with Horizontal(id="triage-status"):
            yield Label("", id="triage-status-label")

    def on_mount(self) -> None:
        table = self.query_one("#triage-table", DataTable)
        table.add_column("Act", key="action", width=6)
        table.add_column("Class", key="classification", width=14)
        table.add_column("Diff", key="difficulty", width=10)
        table.add_column("Reviewer", key="reviewer", width=12)
        table.add_column("Location", key="location", width=25)
        table.add_column("Comment", key="comment")
        self._populate_table()
        self._update_status()

    def _populate_table(self) -> None:
        table = self.query_one("#triage-table", DataTable)
        table.clear()

        for i, s in enumerate(self.selectables):
            src = s.triaged.source
            label, _ = ACTION_LABELS.get(s.action, ("[???]", ""))
            cls_label = CLASS_LABELS.get(s.triaged.classification, "?")
            diff_label = s.triaged.difficulty.value if s.triaged.difficulty else "-"
            location = f"{src.file_path}:{src.line_number}" if src.file_path else "body"
            text = src.text[:60].replace("\n", " ")
            if len(src.text) > 60:
                text += "..."

            table.add_row(
                label,
                cls_label,
                diff_label,
                src.reviewer,
                location,
                text,
                key=str(i),
            )

    def _update_status(self) -> None:
        from collections import Counter

        counts = Counter(s.action.value for s in self.selectables)
        parts = [f"{counts.get(a.value, 0)} {a.value}" for a in TriageAction]
        label = self.query_one("#triage-status-label", Label)
        label.update(f"Actions: {', '.join(parts)}  |  f=fix r=reply s=skip d=disagree e=edit D=done")

    def _update_detail(self) -> None:
        """Update detail panel for current row."""
        table = self.query_one("#triage-table", DataTable)
        panel = self.query_one("#detail-panel", Static)
        if table.cursor_row is None or table.cursor_row >= len(self.selectables):
            panel.update("")
            return

        s = self.selectables[table.cursor_row]
        src = s.triaged.source
        lines = [
            f"[{s.action.value.upper()}] {s.triaged.classification.value}",
            f"Reviewer: {src.reviewer}",
        ]
        if src.file_path:
            lines.append(f"Location: {src.file_path}:{src.line_number}")
        lines.append(f"\nComment: {src.text}")
        if s.triaged.fix_hint:
            lines.append(f"\nHint: {s.triaged.fix_hint}")
        if s.edited_reply:
            lines.append(f"\nReply: {s.edited_reply}")
        panel.update("\n".join(lines))

    def on_data_table_cursor_moved(self, event: DataTable.CursorMoved) -> None:
        self._update_detail()

    def _set_action(self, action: TriageAction) -> None:
        table = self.query_one("#triage-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self.selectables):
            return
        idx = table.cursor_row
        self.selectables[idx].action = action
        label, _ = ACTION_LABELS.get(action, ("[???]", ""))
        table.update_cell(str(idx), "action", label)
        self._update_status()
        self._update_detail()

    def action_set_fix(self) -> None:
        self._set_action(TriageAction.FIX)

    def action_set_reply(self) -> None:
        self._set_action(TriageAction.REPLY)

    def action_set_skip(self) -> None:
        self._set_action(TriageAction.SKIP)

    def action_set_disagree(self) -> None:
        self._set_action(TriageAction.DISAGREE)

    def action_edit_reply(self) -> None:
        table = self.query_one("#triage-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self.selectables):
            return
        idx = table.cursor_row
        s = self.selectables[idx]
        self.app.push_screen(
            EditReplyScreen(s.edited_reply or s.triaged.reply_suggestion),
            lambda text: self._on_reply_edited(idx, text),
        )

    def _on_reply_edited(self, idx: int, text: str | None) -> None:
        if text is not None:
            self.selectables[idx].edited_reply = text
            self._update_detail()

    def action_done(self) -> None:
        self.app.push_screen(
            TriageModePicker(),
            self._on_mode_picked,
        )

    def _on_mode_picked(self, mode: str | None) -> None:
        if mode is not None:
            self.app.exit(self.selectables)


class EditReplyScreen(ModalScreen[str | None]):
    """Modal for editing a reply text."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    CSS = """
    EditReplyScreen {
        align: center middle;
    }

    #edit-dialog {
        width: 70;
        height: 20;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #edit-title {
        text-style: bold;
        text-align: center;
        width: 100%;
        padding-bottom: 1;
    }

    TextArea {
        height: 1fr;
    }
    """

    def __init__(self, initial_text: str = "", name: str | None = None):
        super().__init__(name=name)
        self._initial_text = initial_text

    def compose(self) -> ComposeResult:
        with Container(id="edit-dialog"):
            yield Label("Edit Reply (Ctrl+S to save, Esc to cancel)", id="edit-title")
            yield TextArea(self._initial_text, id="edit-area")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#edit-area", TextArea).focus()

    def action_save(self) -> None:
        text = self.query_one("#edit-area", TextArea).text
        self.dismiss(text)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TriageModePicker(ModalScreen[str | None]):
    """Modal for picking execution mode after triage."""

    BINDINGS = [
        Binding("p", "pick_plan", "Plan", show=False),
        Binding("r", "pick_reply", "Reply", show=False),
        Binding("a", "pick_agent", "Agent", show=False),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    TriageModePicker {
        align: center middle;
    }

    #mode-dialog {
        width: 55;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #mode-title {
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

    def compose(self) -> ComposeResult:
        with Container(id="mode-dialog"):
            yield Label("Execution Mode", id="mode-title")
            yield OptionList(
                Option("\\[P] Plan -- write YAML fix plan", id="plan"),
                Option("\\[R] Reply -- write plan + post replies to RB", id="reply"),
                Option("\\[A] Agent -- write plan + print agent command", id="agent"),
                None,
                Option("\\[Esc] Cancel", id="cancel"),
                id="mode-list",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#mode-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select(str(event.option_id))

    def action_select(self) -> None:
        ol = self.query_one("#mode-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id:
                self._select(str(opt.id))

    def _select(self, option_id: str) -> None:
        if option_id in ("plan", "reply", "agent"):
            self.dismiss(option_id)
        elif option_id == "cancel":
            self.dismiss(None)

    def action_pick_plan(self) -> None:
        self.dismiss("plan")

    def action_pick_reply(self) -> None:
        self.dismiss("reply")

    def action_pick_agent(self) -> None:
        self.dismiss("agent")

    def action_cancel(self) -> None:
        self.dismiss(None)


class TriageApp(App[list[SelectableTriagedComment] | None]):
    """Standalone app for the triage flow, launched from CLI."""

    TITLE = "BB Review Triage"

    BINDINGS = [
        Binding("q", "quit_app", "Quit"),
    ]

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(
        self,
        selectables: list[SelectableTriagedComment],
        default_mode: str = "plan",
    ):
        super().__init__()
        self._selectables = selectables
        self._default_mode = default_mode

    def compose(self) -> ComposeResult:
        yield Header()
        yield TriageScreen(self._selectables, id="triage-pane")
        yield Footer()

    def action_quit_app(self) -> None:
        self.exit(None)
