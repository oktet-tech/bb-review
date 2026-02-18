"""Triage view screen -- pushed Screen wrapping TriageScreen for use within UnifiedApp."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header

from bb_review.triage.models import (
    FixPlan,
    FixPlanItem,
    RBComment,
    SelectableTriagedComment,
)
from bb_review.triage.plan_writer import write_fix_plan
from bb_review.triage.replier import RBReplier

from .triage_screen import TriageScreen


if TYPE_CHECKING:
    from bb_review.config import Config
    from bb_review.rr.rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)


class TriageViewScreen(Screen):
    """Pushed screen for triage within the unified TUI.

    Wraps TriageScreen and handles Done/Cancelled messages by writing the
    fix plan, optionally posting replies, and dismissing back to the main app.
    """

    BINDINGS = [
        Binding("b", "go_back", "Back"),
        Binding("q", "go_back", "Back", show=False),
    ]

    def __init__(
        self,
        selectables: list[SelectableTriagedComment],
        *,
        raw_diff: str = "",
        rr_id: int,
        repo_name: str,
        config: Config | None = None,
        rb_client: ReviewBoardClient | None = None,
        comments: list[RBComment] | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._selectables = selectables
        self._raw_diff = raw_diff
        self._rr_id = rr_id
        self._repo_name = repo_name
        self._config = config
        self._rb_client = rb_client
        self._comments = comments or []

    def compose(self) -> ComposeResult:
        yield Header()
        yield TriageScreen(
            self._selectables,
            raw_diff=self._raw_diff,
            id="triage-pane",
        )
        yield Footer()

    def on_triage_screen_done(self, event: TriageScreen.Done) -> None:
        """Build plan, write yaml, optionally post replies, then dismiss."""
        selectables = event.selectables
        mode = event.mode

        plan = _build_fix_plan(self._rr_id, self._repo_name, selectables)
        output_path = Path(f"triage_{self._rr_id}.yaml")

        try:
            write_fix_plan(plan, output_path)
            msg = f"Plan written: {output_path}"
        except Exception as e:
            logger.error(f"Failed to write plan: {e}")
            self.notify(f"Failed to write plan: {e}", severity="error")
            return

        if mode == "reply" and self._rb_client and self._comments:
            try:
                review_comment_map = {c.comment_id: c.review_id for c in self._comments}
                replier = RBReplier(self._rb_client)
                published = replier.post_replies(self._rr_id, plan.items, review_comment_map)
                if published:
                    msg += f" + {len(published)} replies posted"
            except Exception as e:
                logger.error(f"Failed to post replies: {e}")
                msg += f" (reply posting failed: {e})"

        self.notify(msg, severity="information")
        self.dismiss(msg)

    def on_triage_screen_cancelled(self, event: TriageScreen.Cancelled) -> None:
        self.dismiss(None)

    def action_go_back(self) -> None:
        self.dismiss(None)


def _build_fix_plan(
    rr_id: int,
    repo_name: str,
    selectables: list[SelectableTriagedComment],
) -> FixPlan:
    """Convert selectable triage items to a fix plan."""
    items = []
    for s in selectables:
        items.append(
            FixPlanItem(
                comment_id=s.triaged.source.comment_id,
                action=s.action,
                file_path=s.triaged.source.file_path,
                line_number=s.triaged.source.line_number,
                classification=s.triaged.classification,
                difficulty=s.triaged.difficulty,
                reviewer=s.triaged.source.reviewer,
                original_text=s.triaged.source.text,
                fix_hint=s.triaged.fix_hint,
                reply_text=s.edited_reply,
            )
        )
    return FixPlan(review_request_id=rr_id, repository=repo_name, items=items)
