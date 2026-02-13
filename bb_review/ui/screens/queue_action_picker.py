"""Action picker modal for queue items."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, OptionList, Static
from textual.widgets.option_list import Option


class QueueActionPickerScreen(ModalScreen[tuple[str, list[int]] | None]):
    """Modal screen for picking an action on queue items.

    Dismisses with (action_key, rr_ids) or None if cancelled.
    action_key is one of: 'next', 'ignore', 'done', 'delete'.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("enter", "select", "Select", priority=True),
        Binding("n", "pick_next", "Next", show=False),
        Binding("i", "pick_ignore", "Ignore", show=False),
        Binding("f", "pick_done", "Done", show=False),
        Binding("d", "pick_delete", "Delete", show=False),
    ]

    CSS = """
    QueueActionPickerScreen {
        align: center middle;
    }

    #dialog {
        width: 45;
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

    def __init__(self, rr_ids: list[int], name: str | None = None):
        super().__init__(name=name)
        self.rr_ids = rr_ids

    def compose(self) -> ComposeResult:
        with Container(id="dialog"):
            yield Label("Queue Action", id="title")
            count = len(self.rr_ids)
            if count == 1:
                yield Static(f"r/{self.rr_ids[0]}", id="info")
            else:
                yield Static(f"{count} items selected", id="info")
            yield OptionList(
                Option("\\[N] Mark as Next", id="next"),
                Option("\\[I] Mark as Ignore", id="ignore"),
                Option("\\[F] Mark as Finished", id="done"),
                None,  # Separator
                Option("\\[D] Delete from queue", id="delete"),
                id="action-list",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#action-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select_action(str(event.option_id))

    def action_select(self) -> None:
        option_list = self.query_one("#action-list", OptionList)
        if option_list.highlighted is not None:
            option = option_list.get_option_at_index(option_list.highlighted)
            if option.id:
                self._select_action(str(option.id))

    def _select_action(self, action_id: str) -> None:
        if action_id in ("next", "ignore", "done", "delete"):
            self.dismiss((action_id, self.rr_ids))

    def action_pick_next(self) -> None:
        self._select_action("next")

    def action_pick_ignore(self) -> None:
        self._select_action("ignore")

    def action_pick_done(self) -> None:
        self._select_action("done")

    def action_pick_delete(self) -> None:
        self._select_action("delete")

    def action_cancel(self) -> None:
        self.dismiss(None)
