"""Diff context viewer widget for displaying unified diff hunks."""

import re

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import RichLog, Static


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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

        old_no = new_no = 0
        target_idx: int | None = None
        line_idx = 0

        for line in diff_text.split("\n"):
            m = _HUNK_RE.match(line)
            if m:
                old_no = int(m.group(1))
                new_no = int(m.group(2))
                styled = Text(line)
                styled.stylize("cyan dim")
                log.write(styled)
                line_idx += 1
                continue

            if line.startswith("+"):
                gutter = f"     {new_no:>4} | "
                style = "green"
                is_target = line_number and new_no == line_number
                new_no += 1
            elif line.startswith("-"):
                gutter = f"{old_no:>4}      | "
                style = "red"
                is_target = False
                old_no += 1
            elif line.startswith("\\"):
                styled = Text(line)
                styled.stylize("dim")
                log.write(styled)
                line_idx += 1
                continue
            else:
                gutter = f"{old_no:>4} {new_no:>4} | "
                style = ""
                is_target = line_number and new_no == line_number
                old_no += 1
                new_no += 1

            display = gutter + line
            styled = Text(display)
            if is_target:
                styled.stylize("bold reverse")
                target_idx = line_idx
            elif style:
                styled.stylize(style)
            log.write(styled)
            line_idx += 1

        if target_idx is not None:
            # Defer scroll so RichLog has rendered all lines
            self.call_after_refresh(log.scroll_to, y=target_idx, animate=False)

    def toggle(self) -> None:
        """Show or hide the diff viewer."""
        self.toggle_class("visible")

    @property
    def is_visible(self) -> bool:
        return self.has_class("visible")
