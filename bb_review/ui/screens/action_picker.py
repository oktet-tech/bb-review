"""Action picker screen for selecting an action on an analysis."""

from dataclasses import dataclass
from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, OptionList, Static
from textual.widgets.option_list import Option

from bb_review.db.models import AnalysisListItem


class ActionType(str, Enum):
    """Types of actions available."""

    EXPORT = "export"
    DELETE = "delete"
    MARK_DRAFT = "mark_draft"
    MARK_SUBMITTED = "mark_submitted"
    MARK_OBSOLETE = "mark_obsolete"
    MARK_INVALID = "mark_invalid"


@dataclass
class ActionResult:
    """Result of an action selection."""

    action: ActionType
    analysis_id: int


class ActionPickerScreen(ModalScreen[ActionResult | None]):
    """Modal screen for picking an action on an analysis."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("enter", "select", "Select", priority=True),
    ]

    CSS = """
    ActionPickerScreen {
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

    #analysis-info {
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

    def __init__(self, analysis: AnalysisListItem, count: int = 1, name: str | None = None):
        """Initialize the action picker.

        Args:
            analysis: The analysis to act on (or first of batch)
            count: Number of analyses being acted on
            name: Optional screen name
        """
        super().__init__(name=name)
        self.analysis = analysis
        self.count = count

    def compose(self) -> ComposeResult:
        """Compose the modal dialog."""
        with Container(id="dialog"):
            yield Label("Select Action", id="title")
            if self.count > 1:
                yield Static(f"{self.count} analyses selected", id="analysis-info")
            else:
                yield Static(
                    f"RR #{self.analysis.review_request_id} ({self.analysis.repository})",
                    id="analysis-info",
                )
            yield OptionList(
                Option("Export", id="export"),
                Option("Delete", id="delete"),
                None,  # Separator
                Option("Mark as: Draft", id="mark_draft"),
                Option("Mark as: Submitted", id="mark_submitted"),
                Option("Mark as: Obsolete", id="mark_obsolete"),
                Option("Mark as: Invalid", id="mark_invalid"),
                id="action-list",
            )
        yield Footer()

    def on_mount(self) -> None:
        """Focus the option list on mount."""
        self.query_one("#action-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle option selection."""
        self._select_action(str(event.option_id))

    def action_select(self) -> None:
        """Select the highlighted option."""
        option_list = self.query_one("#action-list", OptionList)
        if option_list.highlighted is not None:
            option = option_list.get_option_at_index(option_list.highlighted)
            if option.id:
                self._select_action(str(option.id))

    def _select_action(self, action_id: str) -> None:
        """Process the selected action."""
        action_map = {
            "export": ActionType.EXPORT,
            "delete": ActionType.DELETE,
            "mark_draft": ActionType.MARK_DRAFT,
            "mark_submitted": ActionType.MARK_SUBMITTED,
            "mark_obsolete": ActionType.MARK_OBSOLETE,
            "mark_invalid": ActionType.MARK_INVALID,
        }
        action_type = action_map.get(action_id)
        if action_type:
            self.dismiss(ActionResult(action=action_type, analysis_id=self.analysis.id))

    def action_cancel(self) -> None:
        """Cancel and dismiss the modal."""
        self.dismiss(None)


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Modal screen for confirming deletion."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }

    #dialog {
        width: 50;
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

    #hint {
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, analysis: AnalysisListItem, count: int = 1, name: str | None = None):
        """Initialize the confirmation dialog.

        Args:
            analysis: The analysis to delete (or first of batch)
            count: Number of analyses to delete
            name: Optional screen name
        """
        super().__init__(name=name)
        self.analysis = analysis
        self.count = count

    def compose(self) -> ComposeResult:
        """Compose the confirmation dialog."""
        with Container(id="dialog"):
            yield Label("Confirm Delete", id="title")
            if self.count > 1:
                yield Static(f"Delete {self.count} analyses?", id="message")
            else:
                yield Static(
                    f"Delete analysis #{self.analysis.id} for RR #{self.analysis.review_request_id}?",
                    id="message",
                )
            yield Static("\\[Y]es / \\[N]o", id="hint")
        yield Footer()

    def action_confirm(self) -> None:
        """Confirm deletion."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel deletion."""
        self.dismiss(False)
