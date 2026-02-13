"""UI-specific models for interactive export."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum

from bb_review.db.models import StoredAnalysis, StoredComment


class CommentStatus(Enum):
    """Three-state status for comments in the TUI picker."""

    INCLUDED = "included"
    DUPLICATE = "duplicate"
    EXCLUDED = "excluded"


@dataclass
class SelectableComment:
    """Comment with selection state and optional edits."""

    comment: StoredComment
    status: CommentStatus = CommentStatus.INCLUDED
    edited_message: str | None = None
    edited_suggestion: str | None = None

    @property
    def selected(self) -> bool:
        """Whether this comment will be submitted."""
        return self.status == CommentStatus.INCLUDED

    @property
    def is_submittable(self) -> bool:
        """Alias for selected -- will be included in submission."""
        return self.status == CommentStatus.INCLUDED

    @property
    def effective_message(self) -> str:
        """Get the message to use (edited or original)."""
        return self.edited_message if self.edited_message is not None else self.comment.message

    @property
    def effective_suggestion(self) -> str | None:
        """Get the suggestion to use (edited or original)."""
        if self.edited_suggestion is not None:
            return self.edited_suggestion if self.edited_suggestion else None
        return self.comment.suggestion

    def toggle(self) -> None:
        """Cycle status: included->excluded->included, duplicate->included->excluded->included."""
        if self.status == CommentStatus.INCLUDED:
            self.status = CommentStatus.EXCLUDED
        else:
            # Both EXCLUDED and DUPLICATE transition to INCLUDED
            self.status = CommentStatus.INCLUDED


@dataclass
class ExportableAnalysis:
    """Analysis with selectable comments for export."""

    analysis: StoredAnalysis
    comments: list[SelectableComment] = field(default_factory=list)
    include_summary: bool = True
    skipped: bool = False

    @classmethod
    def from_stored(cls, analysis: StoredAnalysis) -> "ExportableAnalysis":
        """Create from a stored analysis, wrapping all comments."""
        return cls(
            analysis=analysis,
            comments=[SelectableComment(comment=c) for c in analysis.comments],
        )

    @property
    def selected_comments(self) -> list[SelectableComment]:
        """Get only submittable comments."""
        return [c for c in self.comments if c.is_submittable]

    @property
    def selected_count(self) -> int:
        """Count of submittable comments."""
        return len(self.selected_comments)

    @property
    def duplicate_count(self) -> int:
        """Count of duplicate comments."""
        return sum(1 for c in self.comments if c.status == CommentStatus.DUPLICATE)

    @property
    def total_count(self) -> int:
        """Total comment count."""
        return len(self.comments)

    def mark_duplicates(
        self,
        dropped: list,
        threshold: float = 0.6,
    ) -> None:
        """Mark comments that match previously-dropped RB comments.

        Args:
            dropped: List of DroppedComment from dedup module.
            threshold: SequenceMatcher ratio threshold for fuzzy match.
        """
        if not dropped:
            return

        for sc in self.comments:
            # Only mark comments that are currently included (don't override user choices)
            if sc.status != CommentStatus.INCLUDED:
                continue
            for dc in dropped:
                if sc.comment.file_path != dc.file_path:
                    continue
                ratio = SequenceMatcher(None, sc.effective_message, dc.text).ratio()
                if ratio >= threshold:
                    sc.status = CommentStatus.DUPLICATE
                    break
