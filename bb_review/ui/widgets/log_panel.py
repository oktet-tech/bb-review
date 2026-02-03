"""Collapsible log panel with RichLog for background task output."""

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import RichLog, Static


class LogPanel(Container):
    """Log panel that streams background task output."""

    DEFAULT_CSS = """
    LogPanel {
        height: auto;
        max-height: 40%;
        display: none;
        border-top: solid $primary;
    }

    LogPanel.visible {
        display: block;
    }

    LogPanel #log-title {
        height: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    LogPanel RichLog {
        height: 1fr;
        min-height: 5;
        max-height: 20;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Log (L: toggle, C: clear)", id="log-title")
        yield RichLog(id="log-output", wrap=True, markup=True)

    def write(self, text: str) -> None:
        """Append a line to the log."""
        self.query_one("#log-output", RichLog).write(text)

    def clear(self) -> None:
        """Clear all log output."""
        self.query_one("#log-output", RichLog).clear()

    def toggle(self) -> None:
        """Show or hide the log panel."""
        self.toggle_class("visible")

    def show(self) -> None:
        """Make the log panel visible."""
        self.add_class("visible")
