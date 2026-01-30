"""Review request chain resolution for patch series.

This module handles discovering and resolving chains of dependent review requests
using the Review Board API's `depends_on` field.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .rb_client import ReviewBoardClient

logger = logging.getLogger(__name__)


class ChainError(Exception):
    """Base exception for chain resolution errors."""

    pass


class DiamondDependencyError(ChainError):
    """Raised when a review request has multiple dependencies (diamond pattern)."""

    def __init__(self, rr_id: int, depends_on: list[int]):
        self.rr_id = rr_id
        self.depends_on = depends_on
        super().__init__(
            f"Review request r/{rr_id} has multiple dependencies: {depends_on}. "
            f"Use --chain-file to specify explicit ordering."
        )


class DiscardedDependencyError(ChainError):
    """Raised when a dependency is discarded."""

    def __init__(self, rr_id: int):
        self.rr_id = rr_id
        super().__init__(f"Review request r/{rr_id} is discarded. Cannot review chain.")


class CrossRepoDependencyError(ChainError):
    """Raised when a dependency is in a different repository."""

    def __init__(self, rr_id: int, expected_repo: str, actual_repo: str):
        self.rr_id = rr_id
        self.expected_repo = expected_repo
        self.actual_repo = actual_repo
        super().__init__(
            f"Review request r/{rr_id} is in different repository ({actual_repo}). "
            f"Chain must be in same repo ({expected_repo})."
        )


class CircularDependencyError(ChainError):
    """Raised when a circular dependency is detected."""

    def __init__(self, chain: list[int]):
        self.chain = chain
        chain_str = " -> ".join(str(x) for x in chain)
        super().__init__(f"Circular dependency detected: {chain_str}")


class SubmittedCommitNotFoundError(ChainError):
    """Raised when a submitted RR's commit cannot be found in the repository."""

    def __init__(self, rr_id: int, summary: str):
        self.rr_id = rr_id
        self.summary = summary
        super().__init__(
            f"Cannot find commit for submitted r/{rr_id} (summary: '{summary[:50]}...'). "
            f"Use --chain-file with --base-commit."
        )


@dataclass
class ChainedReview:
    """Single review request in a chain."""

    review_request_id: int
    summary: str
    status: str  # pending, submitted, discarded
    diff_revision: int
    raw_diff: str | None = None  # Populated later when needed
    base_commit_id: str | None = None
    needs_review: bool = True  # False if already submitted


@dataclass
class ReviewChain:
    """A chain of dependent review requests.

    The reviews are ordered from base (first to apply) to tip (target review).
    """

    reviews: list[ChainedReview] = field(default_factory=list)
    base_commit: str | None = None  # Commit to checkout before applying patches
    repository: str = ""  # All must be same repo

    @property
    def pending_reviews(self) -> list[ChainedReview]:
        """Get reviews that need to be reviewed (status=pending)."""
        return [r for r in self.reviews if r.needs_review]

    @property
    def target_review(self) -> ChainedReview | None:
        """Get the target (tip) review request."""
        return self.reviews[-1] if self.reviews else None

    def __len__(self) -> int:
        return len(self.reviews)


def resolve_chain(
    rb_client: "ReviewBoardClient",
    target_rr_id: int,
    find_commit_func: Callable[[str, str], str | None] | None = None,
) -> ReviewChain:
    """Resolve the dependency chain for a review request.

    Walks the `depends_on` links backwards to find the root of the chain,
    then returns the chain ordered from base to target.

    Args:
        rb_client: Review Board client for API calls.
        target_rr_id: The review request ID to resolve chain for.
        find_commit_func: Optional function(repo_name, summary) -> commit_sha
            for finding submitted review commits. If None, submitted RRs
            without base_commit_id will raise SubmittedCommitNotFoundError.

    Returns:
        ReviewChain with all reviews in order from base to tip.

    Raises:
        DiamondDependencyError: If any RR has multiple depends_on entries.
        DiscardedDependencyError: If any RR in chain is discarded.
        CrossRepoDependencyError: If dependencies span multiple repos.
        CircularDependencyError: If circular dependency is detected.
        SubmittedCommitNotFoundError: If submitted RR's commit not found.
    """
    logger.info(f"Resolving chain for r/{target_rr_id}")

    # Walk backwards collecting chain
    chain_ids: list[int] = []
    visited: set[int] = set()
    current_id = target_rr_id
    target_repo: str | None = None

    while current_id is not None:
        if current_id in visited:
            # Circular dependency
            chain_ids.append(current_id)
            raise CircularDependencyError(chain_ids)

        visited.add(current_id)
        chain_ids.append(current_id)

        # Fetch RR info
        rr_info = rb_client.get_review_request_info(current_id)
        logger.debug(f"r/{current_id}: status={rr_info.status}, depends_on={rr_info.depends_on}")

        # Validate repository consistency
        if target_repo is None:
            target_repo = rr_info.repository_name
        elif rr_info.repository_name != target_repo:
            raise CrossRepoDependencyError(current_id, target_repo, rr_info.repository_name)

        # Check for discarded
        if rr_info.status == "discarded":
            raise DiscardedDependencyError(current_id)

        # Check for diamond dependency
        if len(rr_info.depends_on) > 1:
            raise DiamondDependencyError(current_id, rr_info.depends_on)

        # Stop conditions
        if rr_info.status == "submitted":
            # Submitted RR is the base - stop here
            logger.debug(f"r/{current_id} is submitted, stopping chain walk")
            break

        if not rr_info.depends_on:
            # No more dependencies - this is the root
            logger.debug(f"r/{current_id} has no dependencies, this is the root")
            break

        # Continue to next dependency
        current_id = rr_info.depends_on[0]

    # Reverse to get base->tip order
    chain_ids.reverse()

    # Build the chain with full info
    chain = ReviewChain(repository=target_repo or "")
    base_commit: str | None = None

    for rr_id in chain_ids:
        rr_info = rb_client.get_review_request_info(rr_id)

        # Determine if this RR needs review
        needs_review = rr_info.status == "pending"

        # For submitted RRs, find the commit
        if rr_info.status == "submitted":
            if rr_info.base_commit_id:
                # Use base_commit_id of next RR if available
                pass
            elif find_commit_func:
                # Try to find commit by summary
                commit = find_commit_func(target_repo, rr_info.summary)
                if commit:
                    # This submitted RR's commit becomes the base for the rest
                    base_commit = commit
                else:
                    raise SubmittedCommitNotFoundError(rr_id, rr_info.summary)
            else:
                raise SubmittedCommitNotFoundError(rr_id, rr_info.summary)

        # For the first pending RR, use its base_commit_id as chain base
        if needs_review and base_commit is None and rr_info.base_commit_id:
            base_commit = rr_info.base_commit_id

        review = ChainedReview(
            review_request_id=rr_id,
            summary=rr_info.summary,
            status=rr_info.status,
            diff_revision=rr_info.diff_revision,
            base_commit_id=rr_info.base_commit_id,
            needs_review=needs_review,
        )
        chain.reviews.append(review)

    chain.base_commit = base_commit

    logger.info(f"Resolved chain: {[r.review_request_id for r in chain.reviews]}")
    logger.info(f"  Pending reviews: {[r.review_request_id for r in chain.pending_reviews]}")
    logger.info(f"  Base commit: {chain.base_commit}")

    return chain


def load_chain_from_file(
    rb_client: "ReviewBoardClient",
    chain_file_path: str,
    base_commit: str | None = None,
) -> ReviewChain:
    """Load a chain from a file with explicit RR IDs.

    The file should contain one review request ID per line (or URL).
    Order is from base to tip (first line applied first).

    Args:
        rb_client: Review Board client for API calls.
        chain_file_path: Path to file with RR IDs.
        base_commit: Optional base commit to start from.

    Returns:
        ReviewChain with reviews in file order.

    Raises:
        ValueError: If file is empty or contains invalid IDs.
        CrossRepoDependencyError: If RRs span multiple repos.
        DiscardedDependencyError: If any RR is discarded.
    """
    from pathlib import Path
    import re

    chain_file = Path(chain_file_path)
    if not chain_file.exists():
        raise ValueError(f"Chain file not found: {chain_file_path}")

    rr_ids: list[int] = []
    url_pattern = re.compile(r"/r/(\d+)(?:/|$)")

    for line in chain_file.read_text().strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Try to parse as URL
        match = url_pattern.search(line)
        if match:
            rr_ids.append(int(match.group(1)))
        else:
            # Try to parse as plain number
            try:
                rr_ids.append(int(line))
            except ValueError as e:
                raise ValueError(f"Invalid review request ID or URL: {line}") from e

    if not rr_ids:
        raise ValueError(f"No review request IDs found in {chain_file_path}")

    logger.info(f"Loaded {len(rr_ids)} review IDs from {chain_file_path}")

    # Build chain from explicit list
    chain = ReviewChain()
    target_repo: str | None = None

    for rr_id in rr_ids:
        rr_info = rb_client.get_review_request_info(rr_id)

        # Validate repository consistency
        if target_repo is None:
            target_repo = rr_info.repository_name
            chain.repository = target_repo
        elif rr_info.repository_name != target_repo:
            raise CrossRepoDependencyError(rr_id, target_repo, rr_info.repository_name)

        # Check for discarded
        if rr_info.status == "discarded":
            raise DiscardedDependencyError(rr_id)

        review = ChainedReview(
            review_request_id=rr_id,
            summary=rr_info.summary,
            status=rr_info.status,
            diff_revision=rr_info.diff_revision,
            base_commit_id=rr_info.base_commit_id,
            needs_review=rr_info.status == "pending",
        )
        chain.reviews.append(review)

    # Set base commit
    if base_commit:
        chain.base_commit = base_commit
    elif chain.reviews and chain.reviews[0].base_commit_id:
        chain.base_commit = chain.reviews[0].base_commit_id

    logger.info(f"Built chain from file: {[r.review_request_id for r in chain.reviews]}")

    return chain
