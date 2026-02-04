"""ReviewSession dataclass to bundle common review orchestration params."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..git import RepoManager
from ..rr import ReviewBoardClient
from ..rr.chain import ChainedReview


# (rr_id, summary, raw_diff, repo_path, repo_config, at_reviewed_state) -> analysis_text
ReviewerFn = Callable[[int, str, str, Path, object, bool], str]

# (reviews, base_ref, repo_path, repo_config) -> analysis_text
SeriesReviewerFn = Callable[[list[ChainedReview], str, Path, object], str]


@dataclass
class ReviewSession:
    """Bundles the shared context that every review invocation needs.

    Eliminates repeated parameter passing between run_review_command,
    _run_single_review, _run_chain_review, and _run_series_review.
    """

    config: Config
    rb_client: ReviewBoardClient
    repo_manager: RepoManager
    repo_config: object  # RepositoryConfig
    method_label: str  # "OpenCode", "Claude Code"
    analysis_method: str  # "opencode", "claude_code"
    model: str | None
    default_model: str | None
    fake_review: bool
    reviewer_fn: ReviewerFn
    series_reviewer_fn: SeriesReviewerFn | None = field(default=None)
