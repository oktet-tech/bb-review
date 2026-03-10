"""Collapsible log panel with RichLog for background task output."""

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Label, RichLog


class LogPanel(Container):
    """Log panel that streams background task output."""

    DEFAULT_CSS = """
    LogPanel {
        height: 12;
        max-height: 40%;
        display: none;
        dock: bottom;
        border-top: solid $primary;
    }

    LogPanel.visible {
        display: block;
    }

    LogPanel #log-title-bar {
        height: 1;
        background: $primary;
    }

    LogPanel #log-title-left {
        width: 1fr;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    LogPanel #log-title-tasks {
        width: auto;
        color: $text 70%;
        padding: 0 1;
    }

    LogPanel RichLog {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tasks: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="log-title-bar"):
            yield Label("Log (L: toggle, C: clear)", id="log-title-left")
            yield Label("", id="log-title-tasks")
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

    def add_task(self, key: str, label: str) -> None:
        """Register an active task and refresh the display."""
        self._tasks[key] = label
        self._refresh_tasks()

    def remove_task(self, key: str) -> None:
        """Remove a finished task and refresh the display."""
        self._tasks.pop(key, None)
        self._refresh_tasks()

    def _refresh_tasks(self) -> None:
        """Update the right-side task queue label."""
        text = " | ".join(self._tasks.values()) if self._tasks else ""
        self.query_one("#log-title-tasks", Label).update(text)
