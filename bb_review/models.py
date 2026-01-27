"""Data models for BB Review."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class ReviewFocus(str, Enum):
    """Types of issues to focus on during review."""

    BUGS = "bugs"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"
    ARCHITECTURE = "architecture"


class Severity(str, Enum):
    """Severity levels for review comments."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RepoConfig:
    """Configuration for a single repository."""

    name: str
    local_path: Path
    remote_url: str
    rb_repo_name: str  # Name as it appears in Review Board
    default_branch: str = "main"
    repo_type: Optional[str] = None  # e.g., "te-test-suite" for OpenCode MCP setup

    def __post_init__(self):
        if isinstance(self.local_path, str):
            self.local_path = Path(self.local_path)


@dataclass
class ReviewGuidelines:
    """Per-repository review guidelines from .ai-review.yaml."""

    focus: list[ReviewFocus] = field(default_factory=lambda: [ReviewFocus.BUGS, ReviewFocus.SECURITY])
    context: str = ""
    ignore_paths: list[str] = field(default_factory=list)
    severity_threshold: Severity = Severity.MEDIUM
    custom_rules: list[str] = field(default_factory=list)

    @classmethod
    def default(cls) -> "ReviewGuidelines":
        """Return default guidelines when .ai-review.yaml is missing."""
        return cls()


@dataclass
class ReviewComment:
    """A single review comment to be posted."""

    file_path: str
    line_number: int
    message: str
    severity: Severity
    issue_type: ReviewFocus
    suggestion: Optional[str] = None  # Suggested fix if applicable


@dataclass
class ReviewResult:
    """Complete result of analyzing a review request."""

    review_request_id: int
    diff_revision: int
    comments: list[ReviewComment]
    summary: str
    has_critical_issues: bool = False
    analyzed_at: datetime = field(default_factory=datetime.now)

    @property
    def issue_count(self) -> int:
        return len(self.comments)

    @property
    def should_block(self) -> bool:
        """Whether this review should block submission."""
        return self.has_critical_issues or any(
            c.severity == Severity.CRITICAL for c in self.comments
        )


@dataclass
class PendingReview:
    """A review request pending AI analysis."""

    review_request_id: int
    repository: str
    submitter: str
    summary: str
    diff_revision: int
    base_commit: Optional[str] = None
    branch: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class ProcessedReview:
    """Record of a processed review for state tracking."""

    review_request_id: int
    diff_revision: int
    processed_at: datetime
    success: bool
    error_message: Optional[str] = None
    comment_count: int = 0
