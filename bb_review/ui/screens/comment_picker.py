"""Comment picker screen for selecting individual comments."""

import os
import subprocess
import tempfile

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from bb_review.ui.models import ExportableAnalysis, SelectableComment


class CommentItem(ListItem):
    """A list item representing a selectable comment."""

    def __init__(self, comment: SelectableComment, index: int) -> None:
        """Initialize the comment item.

        Args:
            comment: The selectable comment
            index: Index of this comment in the list
        """
        super().__init__()
        self.comment = comment
        self.index = index

    def compose(self) -> ComposeResult:
        """Compose the comment item."""
        c = self.comment.comment
        checkbox = "[X]" if self.comment.selected else "[ ]"
        severity_colors = {
            "critical": "red",
            "high": "yellow",
            "medium": "cyan",
            "low": "green",
        }
        color = severity_colors.get(c.severity.lower(), "white")

        # Truncate message for display
        msg = self.comment.effective_message[:80]
        if len(self.comment.effective_message) > 80:
            msg += "..."

        # Mark if edited
        edited = " [edited]" if self.comment.edited_message is not None else ""

        yield Static(
            f"{checkbox} [{color}]{c.file_path}:{c.line_number}[/{color}] "
            f"({c.severity}/{c.issue_type}){edited}\n"
            f"    {msg}",
            markup=True,
        )


class CommentPickerScreen(Screen):
    """Screen for picking comments within analyses."""

    BINDINGS = [
        Binding("space", "toggle_comment", "Toggle"),
        Binding("a", "toggle_all", "Select All"),
        Binding("e", "edit_comment", "Edit"),
        Binding("n", "next_analysis", "Next"),
        Binding("enter", "next_analysis", "Next"),
        Binding("p", "prev_analysis", "Previous"),
        Binding("s", "skip_analysis", "Skip"),
        Binding("d", "done", "Done"),
        Binding("q", "quit_app", "Quit"),
        Binding("escape", "quit_app", "Quit"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    CSS = """
    CommentPickerScreen {
        layout: vertical;
    }

    #header-info {
        height: auto;
        padding: 1;
        background: $surface;
    }

    #title {
        text-style: bold;
        color: $text;
    }

    #meta {
        color: $text-muted;
    }

    #progress {
        color: $primary;
        text-style: bold;
    }

    #instructions {
        color: $text-muted;
        margin-top: 1;
    }

    #summary-container {
        height: auto;
        max-height: 6;
        padding: 1;
        background: $surface-darken-1;
        border: solid $primary;
        margin: 0 1;
    }

    #summary-label {
        text-style: italic;
    }

    #comments-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    ListView {
        height: 100%;
    }

    ListView > ListItem {
        padding: 1;
    }

    ListView > ListItem.--highlight {
        background: $primary 30%;
    }

    #status-bar {
        height: auto;
        padding: 1;
        background: $surface;
        dock: bottom;
    }
    """

    def __init__(
        self,
        analyses: list[ExportableAnalysis],
        name: str | None = None,
    ) -> None:
        """Initialize the comment picker screen.

        Args:
            analyses: List of exportable analyses to process
            name: Optional screen name
        """
        super().__init__(name=name)
        self.analyses = analyses
        self.current_index = 0

    @property
    def current_analysis(self) -> ExportableAnalysis:
        """Get the current analysis."""
        return self.analyses[self.current_index]

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        with Vertical():
            with Container(id="header-info"):
                yield Label("", id="title")
                yield Label("", id="meta")
                yield Label("", id="progress")
                yield Static(
                    "[Space] Toggle  [A] All  [E] Edit  [N] Next  [P] Prev  [S] Skip  [D] Done  [Q] Quit",
                    id="instructions",
                )

            with Container(id="summary-container"):
                yield Label("", id="summary-label")

            with Container(id="comments-container"):
                yield ListView(id="comments-list")

            with Horizontal(id="status-bar"):
                yield Label("", id="status-label")

        yield Footer()

    def on_mount(self) -> None:
        """Set up the screen when mounted."""
        self._refresh_display()
        # Focus the ListView for arrow key navigation
        list_view = self.query_one("#comments-list", ListView)
        list_view.focus()

    def _refresh_display(self) -> None:
        """Refresh the display for the current analysis."""
        analysis = self.current_analysis

        # Update title
        title = self.query_one("#title", Label)
        title.update(f"Review RR #{analysis.analysis.review_request_id} - {analysis.analysis.repository}")

        # Update meta
        meta = self.query_one("#meta", Label)
        meta.update(
            f"Diff: {analysis.analysis.diff_revision} | "
            f"Model: {analysis.analysis.model_used} | "
            f"Method: {analysis.analysis.analysis_method.value}"
        )

        # Update progress
        progress = self.query_one("#progress", Label)
        progress.update(f"Analysis {self.current_index + 1} of {len(self.analyses)}")

        # Update summary
        summary = self.query_one("#summary-label", Label)
        summary_text = analysis.analysis.summary[:200]
        if len(analysis.analysis.summary) > 200:
            summary_text += "..."
        summary.update(f"Summary: {summary_text}")

        # Rebuild comments list
        self._rebuild_comments_list()
        self._update_status()

    def _rebuild_comments_list(self) -> None:
        """Rebuild the comments list view."""
        list_view = self.query_one("#comments-list", ListView)
        list_view.clear()

        for i, comment in enumerate(self.current_analysis.comments):
            list_view.append(CommentItem(comment, i))

        # Ensure ListView stays focused
        list_view.focus()

    def _update_status(self) -> None:
        """Update the status bar."""
        label = self.query_one("#status-label", Label)
        analysis = self.current_analysis
        label.update(f"Selected: {analysis.selected_count}/{analysis.total_count} comments")

    def _get_selected_comment_index(self) -> int | None:
        """Get the index of the currently selected comment."""
        list_view = self.query_one("#comments-list", ListView)
        if list_view.highlighted_child is not None:
            if isinstance(list_view.highlighted_child, CommentItem):
                return list_view.highlighted_child.index
        return None

    def action_cursor_up(self) -> None:
        """Move cursor up in the comments list."""
        list_view = self.query_one("#comments-list", ListView)
        if list_view.index is not None and list_view.index > 0:
            list_view.index -= 1
        list_view.focus()

    def action_cursor_down(self) -> None:
        """Move cursor down in the comments list."""
        list_view = self.query_one("#comments-list", ListView)
        if list_view.index is not None:
            if list_view.index < len(list_view.children) - 1:
                list_view.index += 1
        elif len(list_view.children) > 0:
            list_view.index = 0
        list_view.focus()

    def action_toggle_comment(self) -> None:
        """Toggle selection on current comment."""
        idx = self._get_selected_comment_index()
        if idx is not None:
            comment = self.current_analysis.comments[idx]
            comment.selected = not comment.selected
            self._rebuild_comments_list()
            self._update_status()

            # Restore selection
            list_view = self.query_one("#comments-list", ListView)
            if idx < len(list_view.children):
                list_view.index = idx

    def action_toggle_all(self) -> None:
        """Toggle all comment selections."""
        analysis = self.current_analysis
        all_selected = all(c.selected for c in analysis.comments)

        for comment in analysis.comments:
            comment.selected = not all_selected

        self._rebuild_comments_list()
        self._update_status()

    def action_edit_comment(self) -> None:
        """Edit the selected comment in external editor."""
        idx = self._get_selected_comment_index()
        if idx is None:
            self.notify("No comment selected", severity="warning")
            return

        comment = self.current_analysis.comments[idx]
        self._edit_comment_in_editor(comment)

        # Refresh display after editing
        self._rebuild_comments_list()

        # Restore selection
        list_view = self.query_one("#comments-list", ListView)
        if idx < len(list_view.children):
            list_view.index = idx

    def _edit_comment_in_editor(self, comment: SelectableComment) -> None:
        """Open comment in external editor.

        Args:
            comment: The comment to edit
        """
        c = comment.comment

        # Create temp file content
        content = f"""# Comment for: {c.file_path}:{c.line_number}
# Severity: {c.severity} | Type: {c.issue_type}
# Lines starting with # are ignored
# Save and close to apply changes

{comment.effective_message}

---SUGGESTION---
{comment.effective_suggestion or ""}
"""

        # Get editor
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vim"))

        # Create temp file and edit
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            # Suspend the app and run editor
            with self.app.suspend():
                subprocess.run([editor, temp_path], check=True)

            # Read back edited content
            with open(temp_path) as f:
                edited_content = f.read()

            # Parse edited content
            self._parse_edited_content(comment, edited_content)

        except subprocess.CalledProcessError:
            self.notify("Editor exited with error", severity="error")
        except Exception as e:
            self.notify(f"Error editing: {e}", severity="error")
        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def _parse_edited_content(self, comment: SelectableComment, content: str) -> None:
        """Parse edited content back into the comment.

        Args:
            comment: The comment to update
            content: The edited content from the file
        """
        lines = content.split("\n")

        # Filter out comment lines (starting with #)
        non_comment_lines = [line for line in lines if not line.strip().startswith("#")]
        text = "\n".join(non_comment_lines).strip()

        # Split on suggestion marker
        if "---SUGGESTION---" in text:
            parts = text.split("---SUGGESTION---", 1)
            message = parts[0].strip()
            suggestion = parts[1].strip() if len(parts) > 1 else ""
        else:
            message = text
            suggestion = ""

        # Update comment with edits
        if message != comment.comment.message:
            comment.edited_message = message

        if suggestion != (comment.comment.suggestion or ""):
            comment.edited_suggestion = suggestion

    def action_next_analysis(self) -> None:
        """Move to the next analysis."""
        if self.current_index < len(self.analyses) - 1:
            self.current_index += 1
            self._refresh_display()
        else:
            # At the end, notify user to press 'd' to finish
            self.notify("Last analysis. Press [D] to export or [Q] to quit.", severity="information")

    def action_prev_analysis(self) -> None:
        """Move to the previous analysis."""
        if self.current_index > 0:
            self.current_index -= 1
            self._refresh_display()

    def action_skip_analysis(self) -> None:
        """Skip the current analysis (mark all comments unselected)."""
        self.current_analysis.skipped = True
        for comment in self.current_analysis.comments:
            comment.selected = False

        self.notify(f"Skipped RR #{self.current_analysis.analysis.review_request_id}")
        self.action_next_analysis()

    def action_done(self) -> None:
        """Finish and return to export."""
        # Filter out skipped analyses and those with no selected comments
        result = [a for a in self.analyses if not a.skipped and a.selected_count > 0]

        if not result:
            self.notify("No comments selected for export", severity="warning")
            return

        self.dismiss(result)

    def action_quit_app(self) -> None:
        """Quit the application."""
        self.app.exit()
