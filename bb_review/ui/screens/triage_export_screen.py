"""Export format picker modal for triage sessions."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, OptionList
from textual.widgets.option_list import Option
import yaml


if TYPE_CHECKING:
    from bb_review.db.models import StoredTriageSession

logger = logging.getLogger(__name__)


class TriageExportScreen(ModalScreen[str | None]):
    """Modal for picking triage export format: YAML, Markdown, or clipboard."""

    BINDINGS = [
        Binding("y", "pick_yaml", "YAML", show=False),
        Binding("m", "pick_markdown", "Markdown", show=False),
        Binding("c", "pick_clipboard", "Clipboard", show=False),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    TriageExportScreen {
        align: center middle;
    }

    #dialog {
        width: 55;
        height: auto;
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

    OptionList {
        height: auto;
        max-height: 10;
        background: $surface;
    }

    OptionList:focus {
        border: tall $primary;
    }
    """

    def __init__(
        self,
        sessions: list[StoredTriageSession],
        name: str | None = None,
    ):
        super().__init__(name=name)
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        count = len(self.sessions)
        label = f"Export {count} triage session(s)" if count > 1 else "Export triage session"
        with Container(id="dialog"):
            yield Label(label, id="title")
            yield OptionList(
                Option("\\[Y] YAML file", id="yaml"),
                Option("\\[M] Markdown file", id="markdown"),
                Option("\\[C] Copy to clipboard", id="clipboard"),
                None,
                Option("\\[Esc] Cancel", id="cancel"),
                id="format-list",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#format-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select(str(event.option_id))

    def action_select(self) -> None:
        ol = self.query_one("#format-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id:
                self._select(str(opt.id))

    def _select(self, option_id: str) -> None:
        if option_id == "yaml":
            result = self._export_yaml()
            self.dismiss(result)
        elif option_id == "markdown":
            result = self._export_markdown()
            self.dismiss(result)
        elif option_id == "clipboard":
            result = self._export_clipboard()
            self.dismiss(result)
        elif option_id == "cancel":
            self.dismiss(None)

    def _export_yaml(self) -> str:
        """Export sessions as YAML fix plan files."""
        files: list[str] = []
        for session in self.sessions:
            data = _session_to_yaml_dict(session)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = Path(f"triage_{session.review_request_id}_{ts}.yaml")
            path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
            files.append(str(path))
        return f"Exported {len(files)} YAML file(s): {', '.join(files)}"

    def _export_markdown(self) -> str:
        """Export sessions as Markdown files."""
        files: list[str] = []
        for session in self.sessions:
            text = _session_to_markdown(session)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = Path(f"triage_{session.review_request_id}_{ts}.md")
            path.write_text(text)
            files.append(str(path))
        return f"Exported {len(files)} Markdown file(s): {', '.join(files)}"

    def _export_clipboard(self) -> str:
        """Copy session plan text to clipboard."""
        parts = [_session_to_markdown(s) for s in self.sessions]
        text = "\n\n---\n\n".join(parts)
        try:
            import subprocess

            subprocess.run(
                ["pbcopy"],
                input=text.encode(),
                check=True,
                capture_output=True,
            )
            return f"Copied {len(self.sessions)} triage session(s) to clipboard"
        except Exception as e:
            logger.warning("Clipboard copy failed: %s", e)
            return f"Clipboard copy failed: {e}"

    def action_pick_yaml(self) -> None:
        result = self._export_yaml()
        self.dismiss(result)

    def action_pick_markdown(self) -> None:
        result = self._export_markdown()
        self.dismiss(result)

    def action_pick_clipboard(self) -> None:
        result = self._export_clipboard()
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _session_to_yaml_dict(session: StoredTriageSession) -> dict:
    """Convert a stored triage session to a YAML-serializable dict."""
    items = []
    for c in session.comments:
        item: dict = {
            "comment_id": c.rb_comment_id,
            "action": c.action,
            "file_path": c.file_path,
            "line_number": c.line_number,
            "classification": c.classification,
            "difficulty": c.difficulty,
            "reviewer": c.reviewer,
            "original_text": c.text,
            "fix_hint": c.fix_hint or "",
        }
        reply = c.edited_reply or c.reply_suggestion
        if reply:
            item["reply_text"] = reply
        items.append(item)

    return {
        "review_request_id": session.review_request_id,
        "repository": session.repository,
        "created_at": session.analyzed_at.isoformat(),
        "summary": session.summary,
        "items": items,
    }


def _session_to_markdown(session: StoredTriageSession) -> str:
    """Convert a stored triage session to Markdown text."""
    lines = [
        f"# Triage: RR #{session.review_request_id}",
        f"Repository: {session.repository}",
        f"Analyzed: {session.analyzed_at.isoformat()}",
        f"Summary: {session.summary}",
        "",
        f"Fixes: {session.fix_count} | Replies: {session.reply_count} | Skip: {session.skip_count}",
        "",
    ]

    for c in session.comments:
        action = c.action.upper()
        location = f"{c.file_path}:{c.line_number}" if c.file_path else "body"
        lines.append(f"## [{action}] {location}")
        lines.append(f"Reviewer: {c.reviewer}")
        if c.classification:
            lines.append(f"Classification: {c.classification}")
        lines.append(f"\n{c.text}")
        if c.fix_hint:
            lines.append(f"\nHint: {c.fix_hint}")
        reply = c.edited_reply or c.reply_suggestion
        if reply:
            lines.append(f"\nReply: {reply}")
        lines.append("")

    return "\n".join(lines)
