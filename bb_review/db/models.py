"""Database models for the reviews database."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AnalysisStatus(str, Enum):
    """Status of an analysis."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    OBSOLETE = "obsolete"
    INVALID = "invalid"


class AnalysisMethod(str, Enum):
    """Method used for analysis."""

    LLM = "llm"
    OPENCODE = "opencode"


@dataclass
class StoredComment:
    """A review comment stored in the database."""

    id: int
    analysis_id: int
    file_path: str
    line_number: int
    message: str
    severity: str  # low, medium, high, critical
    issue_type: str  # bugs, security, performance, style, architecture
    suggestion: str | None = None


@dataclass
class StoredAnalysis:
    """An analysis stored in the database."""

    id: int
    review_request_id: int
    diff_revision: int
    repository: str
    analyzed_at: datetime
    summary: str
    has_critical_issues: bool
    status: AnalysisStatus
    analysis_method: AnalysisMethod
    model_used: str
    # Optional metadata from Review Board
    base_commit_id: str | None = None
    target_commit_id: str | None = None
    submitter: str | None = None
    rr_summary: str | None = None
    branch: str | None = None
    depends_on: list[int] = field(default_factory=list)
    # Chain info
    chain_id: str | None = None
    chain_position: int | None = None
    # Timestamps
    submitted_at: datetime | None = None
    # Optional extras
    raw_response_path: str | None = None
    # Comments (populated separately)
    comments: list[StoredComment] = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.comments)


@dataclass
class StoredChain:
    """A chain of analyses stored in the database."""

    chain_id: str
    created_at: datetime
    repository: str
    partial: bool = False
    failed_at_rr_id: int | None = None
    branch_name: str | None = None
    # Analyses (populated separately)
    analyses: list[StoredAnalysis] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return sum(a.issue_count for a in self.analyses)

    @property
    def reviewed_count(self) -> int:
        return len(self.analyses)


@dataclass
class AnalysisListItem:
    """Lightweight analysis info for listing."""

    id: int
    review_request_id: int
    diff_revision: int
    repository: str
    analyzed_at: datetime
    status: AnalysisStatus
    analysis_method: AnalysisMethod
    model_used: str
    summary: str
    issue_count: int
    has_critical_issues: bool
    chain_id: str | None = None
    rr_summary: str | None = None


@dataclass
class DBStats:
    """Statistics about the reviews database."""

    total_analyses: int
    by_status: dict[str, int]
    by_repository: dict[str, int]
    by_method: dict[str, int]
    total_comments: int
    total_chains: int
    recent_analyses: list[AnalysisListItem]
