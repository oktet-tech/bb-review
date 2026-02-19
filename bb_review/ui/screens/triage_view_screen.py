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
    CommentClassification,
    Difficulty,
    FixPlan,
    FixPlanItem,
    RBComment,
    SelectableTriagedComment,
    TriageAction,
    TriagedComment,
)
from bb_review.triage.plan_writer import write_fix_plan
from bb_review.triage.replier import RBReplier

from .triage_screen import TriageScreen


if TYPE_CHECKING:
    from bb_review.config import Config
    from bb_review.db.models import StoredTriageComment, StoredTriageSession
    from bb_review.db.review_db import ReviewDatabase
    from bb_review.rr.rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)


class TriageViewScreen(Screen):
    """Pushed screen for triage within the unified TUI.

    Wraps TriageScreen and handles Done/Cancelled messages by saving
    decisions to DB (when available), writing the fix plan, and optionally
    posting replies.
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
        db: ReviewDatabase | None = None,
        triage_id: int | None = None,
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
        self._db = db
        self._triage_id = triage_id

    @classmethod
    def from_stored(
        cls,
        session: StoredTriageSession,
        config: Config | None = None,
        db: ReviewDatabase | None = None,
    ) -> TriageViewScreen:
        """Build a TriageViewScreen from a stored triage session."""
        selectables = [_stored_to_selectable(c) for c in session.comments]
        return cls(
            selectables,
            raw_diff=session.raw_diff or "",
            rr_id=session.review_request_id,
            repo_name=session.repository,
            config=config,
            db=db,
            triage_id=session.id,
        )

    def compose(self) -> ComposeResult:
        yield Header()
        yield TriageScreen(
            self._selectables,
            raw_diff=self._raw_diff,
            id="triage-pane",
        )
        yield Footer()

    def on_triage_screen_done(self, event: TriageScreen.Done) -> None:
        """Build plan, save to DB, write yaml, optionally post replies, then dismiss."""
        selectables = event.selectables
        mode = event.mode

        # Save decisions back to DB if we have a stored session
        if self._db and self._triage_id:
            self._save_decisions_to_db(selectables)

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

    def _save_decisions_to_db(self, selectables: list[SelectableTriagedComment]) -> None:
        """Persist user decisions from selectables back to triage_comments."""
        if not self._db or not self._triage_id:
            return

        session = self._db.get_triage(self._triage_id)
        if not session:
            return

        # Match selectables to stored comments by index (same order)
        for i, selectable in enumerate(selectables):
            if i < len(session.comments):
                stored = session.comments[i]
                self._db.update_triage_comment(
                    comment_id=stored.id,
                    action=selectable.action.value,
                    edited_reply=selectable.edited_reply or "",
                )

        self._db.update_triage_counts(self._triage_id)

        # Mark as reviewed if still draft
        if session.status.value == "draft":
            self._db.update_triage_status(self._triage_id, "reviewed")

    def on_triage_screen_cancelled(self, event: TriageScreen.Cancelled) -> None:
        self.dismiss(None)

    def action_go_back(self) -> None:
        self.dismiss(None)


def _stored_to_selectable(c: StoredTriageComment) -> SelectableTriagedComment:
    """Convert a StoredTriageComment to a SelectableTriagedComment."""
    # Reconstruct the RBComment source
    source = RBComment(
        review_id=c.review_id or 0,
        comment_id=c.rb_comment_id,
        reviewer=c.reviewer,
        text=c.text,
        file_path=c.file_path,
        line_number=c.line_number,
        issue_opened=c.issue_opened,
        is_body_comment=c.is_body_comment,
    )

    # Reconstruct classification + difficulty
    try:
        classification = (
            CommentClassification(c.classification) if c.classification else CommentClassification.VALID
        )
    except ValueError:
        classification = CommentClassification.VALID

    try:
        difficulty = Difficulty(c.difficulty) if c.difficulty else None
    except ValueError:
        difficulty = None

    triaged = TriagedComment(
        source=source,
        classification=classification,
        difficulty=difficulty,
        fix_hint=c.fix_hint or "",
        reply_suggestion=c.reply_suggestion or "",
    )

    try:
        action = TriageAction(c.action) if c.action else TriageAction.SKIP
    except ValueError:
        action = TriageAction.SKIP

    return SelectableTriagedComment(
        triaged=triaged,
        action=action,
        edited_reply=c.edited_reply or "",
    )


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
