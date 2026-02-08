"""Diff context viewer widget for displaying unified diff hunks."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import RichLog, Static


class DiffViewer(Container):
    """Displays a unified diff hunk with syntax coloring.

    Hidden by default. Toggle with .toggle() or add/remove the 'visible' class.
    """

    DEFAULT_CSS = """
    DiffViewer {
        height: 1fr;
        display: none;
        border: solid $primary;
        margin: 0 1;
    }

    DiffViewer.visible {
        display: block;
    }

    DiffViewer #diff-title {
        height: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    DiffViewer RichLog {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Diff Context (v: toggle)", id="diff-title")
        yield RichLog(id="diff-output", wrap=False, markup=False)

    def update_content(
        self,
        diff_text: str | None,
        file_path: str | None = None,
        line_number: int | None = None,
    ) -> None:
        """Replace displayed content with a new diff hunk.

        Args:
            diff_text: Unified diff hunk text, or None for empty/unavailable.
            file_path: File path for the title bar.
            line_number: Line number for the title bar.
        """
        log = self.query_one("#diff-output", RichLog)
        log.clear()

        # Update title
        title = self.query_one("#diff-title", Static)
        if file_path and line_number:
            title.update(f"Diff: {file_path}:{line_number}  (v: toggle)")
        else:
            title.update("Diff Context (v: toggle)")

        if not diff_text:
            log.write(Text("No diff context available", style="dim italic"))
            return

        for line in diff_text.split("\n"):
            styled = Text(line)
            if line.startswith("@@"):
                styled.stylize("cyan dim")
            elif line.startswith("+"):
                styled.stylize("green")
            elif line.startswith("-"):
                styled.stylize("red")
            elif line.startswith("\\"):
                styled.stylize("dim")
            log.write(styled)

    def toggle(self) -> None:
        """Show or hide the diff viewer."""
        self.toggle_class("visible")

    @property
    def is_visible(self) -> bool:
        return self.has_class("visible")
