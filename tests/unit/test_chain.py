"""Tests for review chain resolution module."""

import pytest

from bb_review.rr.chain import (
    ChainedReview,
    CircularDependencyError,
    CrossRepoDependencyError,
    DiamondDependencyError,
    DiscardedDependencyError,
    ReviewChain,
    SubmittedCommitNotFoundError,
    load_chain_from_file,
    resolve_chain,
)
from bb_review.rr.rb_client import ReviewRequestInfo


class MockRBClientForChain:
    """Mock RB client that returns predefined review request info."""

    def __init__(self, review_infos: dict[int, ReviewRequestInfo]):
        self.review_infos = review_infos
        self.url = "https://rb.example.com"

    def get_review_request_info(self, review_request_id: int) -> ReviewRequestInfo:
        if review_request_id not in self.review_infos:
            raise RuntimeError(f"Review request {review_request_id} not found")
        return self.review_infos[review_request_id]


class TestReviewChain:
    """Tests for ReviewChain dataclass."""

    def test_empty_chain(self):
        """Empty chain should have no pending reviews."""
        chain = ReviewChain()
        assert len(chain) == 0
        assert chain.pending_reviews == []
        assert chain.target_review is None

    def test_chain_with_reviews(self):
        """Chain with reviews should track them correctly."""
        chain = ReviewChain(
            repository="test-repo",
            base_commit="abc123",
            reviews=[
                ChainedReview(
                    review_request_id=100,
                    summary="First patch",
                    status="submitted",
                    diff_revision=1,
                    needs_review=False,
                ),
                ChainedReview(
                    review_request_id=101,
                    summary="Second patch",
                    status="pending",
                    diff_revision=1,
                    needs_review=True,
                ),
            ],
        )
        assert len(chain) == 2
        assert len(chain.pending_reviews) == 1
        assert chain.pending_reviews[0].review_request_id == 101
        assert chain.target_review.review_request_id == 101


class TestResolveChain:
    """Tests for resolve_chain function."""

    def test_single_review_no_deps(self):
        """Single review with no dependencies should resolve to itself."""
        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="Single patch",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
            }
        )

        chain = resolve_chain(rb_client, 100)
        assert len(chain) == 1
        assert chain.reviews[0].review_request_id == 100
        assert chain.repository == "test-repo"

    def test_linear_chain(self):
        """Linear chain should resolve in order."""
        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="First patch",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
                101: ReviewRequestInfo(
                    id=101,
                    summary="Second patch",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[100],
                    base_commit_id=None,
                    diff_revision=1,
                ),
                102: ReviewRequestInfo(
                    id=102,
                    summary="Third patch",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[101],
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        chain = resolve_chain(rb_client, 102)
        assert len(chain) == 3
        assert [r.review_request_id for r in chain.reviews] == [100, 101, 102]

    def test_chain_with_submitted_base(self):
        """Chain should stop at submitted review."""
        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="Submitted patch",
                    status="submitted",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
                101: ReviewRequestInfo(
                    id=101,
                    summary="Pending patch",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[100],
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        # Without find_commit_func, should raise error for submitted
        with pytest.raises(SubmittedCommitNotFoundError):
            resolve_chain(rb_client, 101)

        # With find_commit_func, should succeed
        def find_commit(repo, summary):
            return "found_commit_sha"

        chain = resolve_chain(rb_client, 101, find_commit)
        assert len(chain) == 2
        assert chain.reviews[0].needs_review is False
        assert chain.reviews[1].needs_review is True

    def test_diamond_dependency_error(self):
        """Should raise error for diamond dependencies."""
        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="Patch with multiple deps",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[101, 102],  # Diamond!
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        with pytest.raises(DiamondDependencyError) as exc_info:
            resolve_chain(rb_client, 100)

        assert exc_info.value.rr_id == 100
        assert exc_info.value.depends_on == [101, 102]
        assert "--chain-file" in str(exc_info.value)

    def test_discarded_dependency_error(self):
        """Should raise error for discarded dependencies."""
        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="Discarded patch",
                    status="discarded",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
                101: ReviewRequestInfo(
                    id=101,
                    summary="Depends on discarded",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[100],
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        with pytest.raises(DiscardedDependencyError) as exc_info:
            resolve_chain(rb_client, 101)

        assert exc_info.value.rr_id == 100

    def test_cross_repo_dependency_error(self):
        """Should raise error for cross-repo dependencies."""
        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="Patch in different repo",
                    status="pending",
                    repository_name="other-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
                101: ReviewRequestInfo(
                    id=101,
                    summary="Main patch",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[100],
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        with pytest.raises(CrossRepoDependencyError) as exc_info:
            resolve_chain(rb_client, 101)

        assert exc_info.value.rr_id == 100
        assert exc_info.value.expected_repo == "test-repo"
        assert exc_info.value.actual_repo == "other-repo"

    def test_circular_dependency_error(self):
        """Should raise error for circular dependencies."""

        # This is tricky to set up since we need to create a loop
        # We'll use a mock that returns different results on each call
        class CircularMockClient:
            def __init__(self):
                self.url = "https://rb.example.com"
                self.call_count = {}

            def get_review_request_info(self, rr_id: int) -> ReviewRequestInfo:
                self.call_count[rr_id] = self.call_count.get(rr_id, 0) + 1
                if rr_id == 100:
                    return ReviewRequestInfo(
                        id=100,
                        summary="First",
                        status="pending",
                        repository_name="test-repo",
                        depends_on=[101],
                        base_commit_id=None,
                        diff_revision=1,
                    )
                elif rr_id == 101:
                    return ReviewRequestInfo(
                        id=101,
                        summary="Second",
                        status="pending",
                        repository_name="test-repo",
                        depends_on=[100],  # Circular!
                        base_commit_id=None,
                        diff_revision=1,
                    )
                raise RuntimeError(f"Unknown RR {rr_id}")

        rb_client = CircularMockClient()

        with pytest.raises(CircularDependencyError) as exc_info:
            resolve_chain(rb_client, 100)

        assert 100 in exc_info.value.chain
        assert 101 in exc_info.value.chain


class TestLoadChainFromFile:
    """Tests for load_chain_from_file function."""

    def test_load_from_file(self, tmp_path):
        """Should load chain from file with RR IDs."""
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("100\n101\n102\n")

        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="First",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
                101: ReviewRequestInfo(
                    id=101,
                    summary="Second",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id=None,
                    diff_revision=1,
                ),
                102: ReviewRequestInfo(
                    id=102,
                    summary="Third",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        chain = load_chain_from_file(rb_client, str(chain_file))
        assert len(chain) == 3
        assert [r.review_request_id for r in chain.reviews] == [100, 101, 102]

    def test_load_from_file_with_urls(self, tmp_path):
        """Should load chain from file with RB URLs."""
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("""
# Comment line
https://rb.example.com/r/100/
https://rb.example.com/r/101/diff/
102
""")

        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="First",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
                101: ReviewRequestInfo(
                    id=101,
                    summary="Second",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id=None,
                    diff_revision=1,
                ),
                102: ReviewRequestInfo(
                    id=102,
                    summary="Third",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        chain = load_chain_from_file(rb_client, str(chain_file))
        assert len(chain) == 3

    def test_load_with_base_commit(self, tmp_path):
        """Should use provided base commit."""
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("100\n")

        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="Single",
                    status="pending",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id=None,
                    diff_revision=1,
                ),
            }
        )

        chain = load_chain_from_file(rb_client, str(chain_file), base_commit="custom123")
        assert chain.base_commit == "custom123"

    def test_load_empty_file(self, tmp_path):
        """Should raise error for empty file."""
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("")

        with pytest.raises(ValueError, match="No review request IDs found"):
            load_chain_from_file(MockRBClientForChain({}), str(chain_file))

    def test_load_invalid_id(self, tmp_path):
        """Should raise error for invalid ID."""
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("not_a_number\n")

        with pytest.raises(ValueError, match="Invalid review request ID"):
            load_chain_from_file(MockRBClientForChain({}), str(chain_file))

    def test_load_discarded_error(self, tmp_path):
        """Should raise error if chain contains discarded review."""
        chain_file = tmp_path / "chain.txt"
        chain_file.write_text("100\n")

        rb_client = MockRBClientForChain(
            {
                100: ReviewRequestInfo(
                    id=100,
                    summary="Discarded",
                    status="discarded",
                    repository_name="test-repo",
                    depends_on=[],
                    base_commit_id="abc123",
                    diff_revision=1,
                ),
            }
        )

        with pytest.raises(DiscardedDependencyError):
            load_chain_from_file(rb_client, str(chain_file))
