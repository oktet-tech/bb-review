"""Action picker and confirm-delete modals for triage sessions."""

from dataclasses import dataclass
from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, OptionList, Static
from textual.widgets.option_list import Option

from bb_review.db.models import TriageListItem


class TriageActionType(str, Enum):
    OPEN = "open"
    EXPORT = "export"
    DELETE = "delete"
    MARK_DRAFT = "mark_draft"
    MARK_REVIEWED = "mark_reviewed"
    MARK_EXPORTED = "mark_exported"


@dataclass
class TriageActionResult:
    action: TriageActionType
    triage_id: int


class TriageActionPickerScreen(ModalScreen[TriageActionResult | None]):
    """Modal for picking an action on a triage session."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("enter", "select", "Select", priority=True),
        Binding("o", "pick_open", "Open", show=False),
        Binding("e", "pick_export", "Export", show=False),
        Binding("d", "pick_delete", "Delete", show=False),
    ]

    CSS = """
    TriageActionPickerScreen {
        align: center middle;
    }

    #dialog {
        width: 50;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #title {
        text-style: bold;
        text-align: center;
        width: 100%;
        padding-bottom: 1;
    }

    #info {
        color: $text-muted;
        text-align: center;
        padding-bottom: 1;
    }

    OptionList {
        height: auto;
        max-height: 20;
        background: $surface;
    }

    OptionList:focus {
        border: tall $primary;
    }
    """

    def __init__(self, item: TriageListItem, count: int = 1, name: str | None = None):
        super().__init__(name=name)
        self.item = item
        self.count = count

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label("Select Action", id="title")
            if self.count > 1:
                yield Static(f"{self.count} triages selected", id="info")
            else:
                yield Static(
                    f"RR #{self.item.review_request_id} ({self.item.repository})",
                    id="info",
                )
            yield OptionList(
                Option("\\[O] Open", id="open"),
                Option("\\[E] Export", id="export"),
                Option("\\[D] Delete", id="delete"),
                None,
                Option("Mark as: Draft", id="mark_draft"),
                Option("Mark as: Reviewed", id="mark_reviewed"),
                Option("Mark as: Exported", id="mark_exported"),
                id="action-list",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#action-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select_action(str(event.option_id))

    def action_select(self) -> None:
        ol = self.query_one("#action-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id:
                self._select_action(str(opt.id))

    def _select_action(self, action_id: str) -> None:
        action_map = {
            "open": TriageActionType.OPEN,
            "export": TriageActionType.EXPORT,
            "delete": TriageActionType.DELETE,
            "mark_draft": TriageActionType.MARK_DRAFT,
            "mark_reviewed": TriageActionType.MARK_REVIEWED,
            "mark_exported": TriageActionType.MARK_EXPORTED,
        }
        action_type = action_map.get(action_id)
        if action_type:
            self.dismiss(TriageActionResult(action=action_type, triage_id=self.item.id))

    def action_pick_open(self) -> None:
        self._select_action("open")

    def action_pick_export(self) -> None:
        self._select_action("export")

    def action_pick_delete(self) -> None:
        self._select_action("delete")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmTriageDeleteScreen(ModalScreen[bool]):
    """Modal for confirming triage deletion."""

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ConfirmTriageDeleteScreen {
        align: center middle;
    }

    #dialog {
        width: 55;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }

    #title {
        text-style: bold;
        color: $error;
        text-align: center;
        width: 100%;
        padding-bottom: 1;
    }

    #message {
        text-align: center;
        padding-bottom: 1;
    }

    OptionList {
        height: auto;
        max-height: 10;
        background: $surface;
    }

    OptionList:focus {
        border: tall $error;
    }
    """

    def __init__(self, item: TriageListItem, count: int = 1, name: str | None = None):
        super().__init__(name=name)
        self.item = item
        self.count = count

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label("Confirm Delete", id="title")
            if self.count > 1:
                yield Static(f"Delete {self.count} triage sessions?", id="message")
            else:
                yield Static(
                    f"Delete triage #{self.item.id} for RR #{self.item.review_request_id}?",
                    id="message",
                )
            yield OptionList(
                Option("[N] No, cancel", id="no"),
                Option("[Y] Yes, delete", id="yes"),
                id="options-list",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#options-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select(str(event.option_id))

    def action_select(self) -> None:
        ol = self.query_one("#options-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id:
                self._select(str(opt.id))

    def _select(self, option_id: str) -> None:
        if option_id == "yes":
            self.dismiss(True)
        elif option_id == "no":
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
