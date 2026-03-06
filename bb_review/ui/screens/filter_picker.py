"""Filter picker modal for filtering pane items by repo or user."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, OptionList
from textual.widgets.option_list import Option


# Result: ("repo", value) | ("user", value) | ("clear", "") | None (cancelled)
FilterResult = tuple[str, str] | None


class FilterPickerScreen(ModalScreen[FilterResult]):
    """Modal for picking a repo or user filter."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select", "Select", priority=True),
    ]

    CSS = """
    FilterPickerScreen {
        align: center middle;
    }

    #filter-dialog {
        width: 50;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #filter-title {
        text-style: bold;
        text-align: center;
        width: 100%;
        padding-bottom: 1;
    }

    #filter-subtitle {
        color: $text-muted;
        text-align: center;
        padding-bottom: 1;
    }

    #filter-list {
        height: auto;
        max-height: 20;
        background: $surface;
    }

    #filter-list:focus {
        border: tall $primary;
    }
    """

    def __init__(
        self,
        repos: list[str],
        users: list[str] | None = None,
        active_filter: tuple[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.repos = sorted(repos)
        self.users = sorted(users) if users else []
        self.active_filter = active_filter

    def compose(self) -> ComposeResult:
        options: list[Option | None] = []

        if self.active_filter:
            kind, value = self.active_filter
            options.append(Option(f"[bold]Clear filter[/bold] ({kind}: {value})", id="clear"))
            options.append(None)

        for repo in self.repos:
            marker = " *" if self.active_filter == ("repo", repo) else ""
            options.append(Option(f"Repo: {repo}{marker}", id=f"repo:{repo}"))

        if self.users:
            options.append(None)
            for user in self.users:
                marker = " *" if self.active_filter == ("user", user) else ""
                options.append(Option(f"User: {user}{marker}", id=f"user:{user}"))

        with Container(id="filter-dialog"):
            yield Label("Filter", id="filter-title")
            if self.active_filter:
                kind, value = self.active_filter
                yield Label(f"Active: {kind} = {value}", id="filter-subtitle")
            yield OptionList(*options, id="filter-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#filter-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._handle_option(str(event.option_id))

    def action_select(self) -> None:
        option_list = self.query_one("#filter-list", OptionList)
        if option_list.highlighted is not None:
            option = option_list.get_option_at_index(option_list.highlighted)
            if option.id:
                self._handle_option(str(option.id))

    def _handle_option(self, option_id: str) -> None:
        if option_id == "clear":
            self.dismiss(("clear", ""))
        elif ":" in option_id:
            kind, value = option_id.split(":", 1)
            self.dismiss((kind, value))

    def action_cancel(self) -> None:
        self.dismiss(None)
