"""UI-specific models for interactive export."""

from dataclasses import dataclass, field

from bb_review.db.models import StoredAnalysis, StoredComment


@dataclass
class SelectableComment:
    """Comment with selection state and optional edits."""

    comment: StoredComment
    selected: bool = True
    edited_message: str | None = None
    edited_suggestion: str | None = None

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
        """Get only selected comments."""
        return [c for c in self.comments if c.selected]

    @property
    def selected_count(self) -> int:
        """Count of selected comments."""
        return len(self.selected_comments)

    @property
    def total_count(self) -> int:
        """Total comment count."""
        return len(self.comments)
