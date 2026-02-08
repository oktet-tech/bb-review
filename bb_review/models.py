"""Data models for BB Review."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


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
    repo_type: str | None = None  # e.g., "te-test-suite" for OpenCode MCP setup

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
    suggestion: str | None = None  # Suggested fix if applicable
    diff_context: str | None = None  # Unified diff hunk around this comment's line


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
        return self.has_critical_issues or any(c.severity == Severity.CRITICAL for c in self.comments)


@dataclass
class ChainReviewResult:
    """Result of reviewing a patch series (chain of review requests).

    Contains results for each review in the chain, along with metadata
    about the chain processing.
    """

    chain_id: str  # Identifies this chain review session (e.g., "42762_20260130_120000")
    reviews: list[ReviewResult] = field(default_factory=list)  # One per RR reviewed
    partial: bool = False  # True if some patches failed to apply
    failed_at_rr_id: int | None = None  # RR ID of first failed patch, if any
    branch_name: str | None = None  # Git branch name if --keep-branch was used
    repository: str = ""

    @property
    def total_issues(self) -> int:
        """Total number of issues across all reviews."""
        return sum(r.issue_count for r in self.reviews)

    @property
    def reviewed_count(self) -> int:
        """Number of successfully reviewed patches."""
        return len(self.reviews)

    def add_review(self, review: ReviewResult) -> None:
        """Add a review result to the chain."""
        self.reviews.append(review)


@dataclass
class PendingReview:
    """A review request pending AI analysis."""

    review_request_id: int
    repository: str
    submitter: str
    summary: str
    diff_revision: int
    base_commit: str | None = None
    branch: str | None = None
    created_at: datetime | None = None


@dataclass
class ProcessedReview:
    """Record of a processed review for state tracking."""

    review_request_id: int
    diff_revision: int
    processed_at: datetime
    success: bool
    error_message: str | None = None
    comment_count: int = 0
