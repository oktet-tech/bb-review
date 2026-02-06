"""Data models for comment triage and fix planning."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class CommentClassification(str, Enum):
    """LLM classification of a review comment."""

    VALID = "valid"
    CONFUSED = "confused"
    NITPICK = "nitpick"
    OUTDATED = "outdated"
    ALREADY_FIXED = "already_fixed"
    DUPLICATE = "duplicate"


class Difficulty(str, Enum):
    """Estimated difficulty of fixing a comment."""

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class TriageAction(str, Enum):
    """User-chosen action for a triaged comment."""

    FIX = "fix"
    REPLY = "reply"
    SKIP = "skip"
    DISAGREE = "disagree"


@dataclass
class RBComment:
    """A single comment fetched from Review Board."""

    review_id: int
    comment_id: int
    reviewer: str
    text: str
    file_path: str | None = None
    line_number: int | None = None
    issue_opened: bool = False
    issue_status: str | None = None
    reply_to_id: int | None = None
    is_body_comment: bool = False


@dataclass
class TriagedComment:
    """A comment after LLM triage classification."""

    source: RBComment
    classification: CommentClassification
    difficulty: Difficulty | None = None
    fix_hint: str = ""
    reply_suggestion: str = ""


@dataclass
class TriageResult:
    """Complete triage result for a review request."""

    review_request_id: int
    triaged_comments: list[TriagedComment] = field(default_factory=list)
    summary: str = ""


@dataclass
class FixPlanItem:
    """A single item in a fix plan."""

    comment_id: int
    action: TriageAction
    file_path: str | None = None
    line_number: int | None = None
    classification: CommentClassification | None = None
    difficulty: Difficulty | None = None
    reviewer: str = ""
    original_text: str = ""
    fix_hint: str = ""
    reply_text: str = ""


@dataclass
class FixPlan:
    """Complete fix plan for a review request."""

    review_request_id: int
    repository: str
    created_at: datetime = field(default_factory=datetime.now)
    items: list[FixPlanItem] = field(default_factory=list)

    @property
    def fix_count(self) -> int:
        return sum(1 for i in self.items if i.action == TriageAction.FIX)

    @property
    def reply_count(self) -> int:
        return sum(1 for i in self.items if i.action in (TriageAction.REPLY, TriageAction.DISAGREE))

    @property
    def skip_count(self) -> int:
        return sum(1 for i in self.items if i.action == TriageAction.SKIP)


@dataclass
class SelectableTriagedComment:
    """Triaged comment with user-selected action (for TUI state)."""

    triaged: TriagedComment
    action: TriageAction
    edited_reply: str = ""

    @classmethod
    def from_triaged(cls, triaged: TriagedComment) -> "SelectableTriagedComment":
        """Create with default action based on classification."""
        defaults = {
            CommentClassification.VALID: TriageAction.FIX,
            CommentClassification.CONFUSED: TriageAction.REPLY,
            CommentClassification.NITPICK: TriageAction.SKIP,
            CommentClassification.OUTDATED: TriageAction.REPLY,
            CommentClassification.ALREADY_FIXED: TriageAction.REPLY,
            CommentClassification.DUPLICATE: TriageAction.SKIP,
        }
        action = defaults.get(triaged.classification, TriageAction.SKIP)
        reply = triaged.reply_suggestion
        return cls(triaged=triaged, action=action, edited_reply=reply)
