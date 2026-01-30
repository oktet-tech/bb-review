"""Tests for the reviews database module."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bb_review.db import (
    AnalysisMethod,
    AnalysisStatus,
    ReviewDatabase,
    export_to_json,
    export_to_markdown,
)
from bb_review.models import ReviewComment, ReviewFocus, ReviewResult, Severity


@pytest.fixture
def temp_db(tmp_path: Path) -> ReviewDatabase:
    """Create a temporary reviews database."""
    db_path = tmp_path / "test_reviews.db"
    return ReviewDatabase(db_path)


@pytest.fixture
def sample_review_result() -> ReviewResult:
    """Create a sample ReviewResult for testing."""
    return ReviewResult(
        review_request_id=42738,
        diff_revision=1,
        comments=[
            ReviewComment(
                file_path="src/main.c",
                line_number=42,
                message="Potential null pointer dereference",
                severity=Severity.HIGH,
                issue_type=ReviewFocus.BUGS,
                suggestion="Add null check before use",
            ),
            ReviewComment(
                file_path="src/utils.c",
                line_number=15,
                message="Consider using safer string function",
                severity=Severity.MEDIUM,
                issue_type=ReviewFocus.SECURITY,
                suggestion="Use strncpy instead of strcpy",
            ),
        ],
        summary="Found 2 issues: 1 high, 1 medium severity",
        has_critical_issues=False,
    )


@pytest.fixture
def sample_review_result_critical() -> ReviewResult:
    """Create a sample ReviewResult with critical issues."""
    return ReviewResult(
        review_request_id=42739,
        diff_revision=2,
        comments=[
            ReviewComment(
                file_path="src/auth.c",
                line_number=100,
                message="SQL injection vulnerability",
                severity=Severity.CRITICAL,
                issue_type=ReviewFocus.SECURITY,
                suggestion="Use parameterized queries",
            ),
        ],
        summary="Found critical security issue",
        has_critical_issues=True,
    )


class TestReviewDatabase:
    """Tests for ReviewDatabase class."""

    def test_create_database(self, temp_db: ReviewDatabase):
        """Database file is created on init."""
        assert temp_db.db_path.exists()

    def test_save_analysis(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Save an analysis and retrieve it."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        assert analysis_id == 1

        # Retrieve and verify
        analysis = temp_db.get_analysis(analysis_id)
        assert analysis is not None
        assert analysis.review_request_id == 42738
        assert analysis.diff_revision == 1
        assert analysis.repository == "test-repo"
        assert analysis.analysis_method == AnalysisMethod.LLM
        assert analysis.model_used == "claude-sonnet-4"
        assert analysis.status == AnalysisStatus.DRAFT
        assert analysis.summary == "Found 2 issues: 1 high, 1 medium severity"
        assert len(analysis.comments) == 2

    def test_save_analysis_with_chain(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Save an analysis with chain information."""
        # First create the chain
        with temp_db._connection() as conn:
            conn.execute(
                """
                INSERT INTO chains (chain_id, created_at, repository, partial)
                VALUES (?, ?, ?, ?)
                """,
                ("42738_20260130_120000", datetime.now().isoformat(), "test-repo", 0),
            )

        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
            chain_id="42738_20260130_120000",
            chain_position=1,
        )

        analysis = temp_db.get_analysis(analysis_id)
        assert analysis is not None
        assert analysis.chain_id == "42738_20260130_120000"
        assert analysis.chain_position == 1

    def test_save_opencode_analysis(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Save an OpenCode analysis."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="opencode",
            model="claude-sonnet-4",
        )

        analysis = temp_db.get_analysis(analysis_id)
        assert analysis is not None
        assert analysis.analysis_method == AnalysisMethod.OPENCODE

    def test_get_analysis_not_found(self, temp_db: ReviewDatabase):
        """Return None for non-existent analysis."""
        analysis = temp_db.get_analysis(999)
        assert analysis is None

    def test_get_analysis_by_rr(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Get analysis by review request ID."""
        temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        analysis = temp_db.get_analysis_by_rr(42738)
        assert analysis is not None
        assert analysis.review_request_id == 42738

    def test_get_analysis_by_rr_with_diff_revision(
        self, temp_db: ReviewDatabase, sample_review_result: ReviewResult
    ):
        """Get analysis by RR ID and specific diff revision."""
        temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        # Create another with diff revision 2
        result2 = ReviewResult(
            review_request_id=42738,
            diff_revision=2,
            comments=[],
            summary="No issues found",
        )
        temp_db.save_analysis(
            result=result2,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        # Should get the specific revision
        analysis = temp_db.get_analysis_by_rr(42738, diff_revision=1)
        assert analysis is not None
        assert analysis.diff_revision == 1

    def test_list_analyses(
        self,
        temp_db: ReviewDatabase,
        sample_review_result: ReviewResult,
        sample_review_result_critical: ReviewResult,
    ):
        """List all analyses."""
        temp_db.save_analysis(
            result=sample_review_result,
            repository="repo-a",
            analysis_method="llm",
            model="claude-sonnet-4",
        )
        temp_db.save_analysis(
            result=sample_review_result_critical,
            repository="repo-b",
            analysis_method="opencode",
            model="grok-code-fast",
        )

        analyses = temp_db.list_analyses()
        assert len(analyses) == 2

    def test_list_analyses_by_repository(
        self,
        temp_db: ReviewDatabase,
        sample_review_result: ReviewResult,
        sample_review_result_critical: ReviewResult,
    ):
        """Filter analyses by repository."""
        temp_db.save_analysis(
            result=sample_review_result,
            repository="repo-a",
            analysis_method="llm",
            model="claude-sonnet-4",
        )
        temp_db.save_analysis(
            result=sample_review_result_critical,
            repository="repo-b",
            analysis_method="opencode",
            model="grok-code-fast",
        )

        analyses = temp_db.list_analyses(repository="repo-a")
        assert len(analyses) == 1
        assert analyses[0].repository == "repo-a"

    def test_list_analyses_by_status(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Filter analyses by status."""
        id1 = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )
        temp_db.mark_submitted(id1)

        # Create another draft
        result2 = ReviewResult(
            review_request_id=42740,
            diff_revision=1,
            comments=[],
            summary="No issues",
        )
        temp_db.save_analysis(
            result=result2,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        drafts = temp_db.list_analyses(status="draft")
        assert len(drafts) == 1
        assert drafts[0].review_request_id == 42740

        submitted = temp_db.list_analyses(status="submitted")
        assert len(submitted) == 1
        assert submitted[0].review_request_id == 42738

    def test_list_analyses_limit(self, temp_db: ReviewDatabase):
        """Limit number of returned analyses."""
        for i in range(10):
            result = ReviewResult(
                review_request_id=42700 + i,
                diff_revision=1,
                comments=[],
                summary=f"Review {i}",
            )
            temp_db.save_analysis(
                result=result,
                repository="test-repo",
                analysis_method="llm",
                model="claude",
            )

        analyses = temp_db.list_analyses(limit=5)
        assert len(analyses) == 5

    def test_mark_submitted(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Mark analysis as submitted."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        temp_db.mark_submitted(analysis_id)

        analysis = temp_db.get_analysis(analysis_id)
        assert analysis is not None
        assert analysis.status == AnalysisStatus.SUBMITTED
        assert analysis.submitted_at is not None

    def test_mark_abandoned(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Mark analysis as abandoned."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        temp_db.mark_abandoned(analysis_id)

        analysis = temp_db.get_analysis(analysis_id)
        assert analysis is not None
        assert analysis.status == AnalysisStatus.ABANDONED

    def test_update_status(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Update analysis status."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        temp_db.update_status(analysis_id, "submitted")

        analysis = temp_db.get_analysis(analysis_id)
        assert analysis is not None
        assert analysis.status == AnalysisStatus.SUBMITTED

    def test_update_status_invalid(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Error on invalid status."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        with pytest.raises(ValueError, match="Invalid status"):
            temp_db.update_status(analysis_id, "invalid_status")

    def test_get_stats(
        self,
        temp_db: ReviewDatabase,
        sample_review_result: ReviewResult,
        sample_review_result_critical: ReviewResult,
    ):
        """Get database statistics."""
        id1 = temp_db.save_analysis(
            result=sample_review_result,
            repository="repo-a",
            analysis_method="llm",
            model="claude-sonnet-4",
        )
        temp_db.save_analysis(
            result=sample_review_result_critical,
            repository="repo-b",
            analysis_method="opencode",
            model="grok-code-fast",
        )
        temp_db.mark_submitted(id1)

        stats = temp_db.get_stats()

        assert stats.total_analyses == 2
        assert stats.total_comments == 3  # 2 + 1
        assert stats.by_status["draft"] == 1
        assert stats.by_status["submitted"] == 1
        assert stats.by_repository["repo-a"] == 1
        assert stats.by_repository["repo-b"] == 1
        assert stats.by_method["llm"] == 1
        assert stats.by_method["opencode"] == 1

    def test_cleanup(self, temp_db: ReviewDatabase):
        """Remove old analyses."""
        # Create old analysis (manually set old date)
        old_result = ReviewResult(
            review_request_id=42700,
            diff_revision=1,
            comments=[],
            summary="Old review",
            analyzed_at=datetime.now() - timedelta(days=100),
        )
        temp_db.save_analysis(
            result=old_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude",
        )

        # Create recent analysis
        new_result = ReviewResult(
            review_request_id=42701,
            diff_revision=1,
            comments=[],
            summary="New review",
        )
        temp_db.save_analysis(
            result=new_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude",
        )

        # Cleanup analyses older than 90 days
        count = temp_db.cleanup(90)

        # Note: cleanup removes by analyzing analyzed_at timestamp
        # The first one should be removed
        assert count == 1

        # Only the new one should remain
        analyses = temp_db.list_analyses()
        assert len(analyses) == 1
        assert analyses[0].review_request_id == 42701


class TestReviewDatabaseChains:
    """Tests for chain functionality in ReviewDatabase."""

    def test_save_chain(self, temp_db: ReviewDatabase):
        """Save a chain of analyses."""
        from bb_review.models import ChainReviewResult

        chain = ChainReviewResult(
            chain_id="42738_20260130_120000",
            repository="test-repo",
        )

        # Add reviews to chain
        for i in range(3):
            result = ReviewResult(
                review_request_id=42738 + i,
                diff_revision=1,
                comments=[],
                summary=f"Review {i}",
            )
            chain.add_review(result)

        chain_id = temp_db.save_chain(
            chain_result=chain,
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        assert chain_id == "42738_20260130_120000"

        # Verify chain was saved
        stored_chain = temp_db.get_chain(chain_id)
        assert stored_chain is not None
        assert stored_chain.repository == "test-repo"
        assert len(stored_chain.analyses) == 3

    def test_get_chain_not_found(self, temp_db: ReviewDatabase):
        """Return None for non-existent chain."""
        chain = temp_db.get_chain("nonexistent_chain")
        assert chain is None

    def test_list_analyses_by_chain(self, temp_db: ReviewDatabase):
        """Filter analyses by chain ID."""
        from bb_review.models import ChainReviewResult

        chain = ChainReviewResult(
            chain_id="42738_20260130_120000",
            repository="test-repo",
        )

        for i in range(2):
            result = ReviewResult(
                review_request_id=42738 + i,
                diff_revision=1,
                comments=[],
                summary=f"Review {i}",
            )
            chain.add_review(result)

        temp_db.save_chain(
            chain_result=chain,
            analysis_method="llm",
            model="claude",
        )

        # Add standalone analysis
        standalone = ReviewResult(
            review_request_id=42800,
            diff_revision=1,
            comments=[],
            summary="Standalone",
        )
        temp_db.save_analysis(
            result=standalone,
            repository="test-repo",
            analysis_method="llm",
            model="claude",
        )

        chain_analyses = temp_db.list_analyses(chain_id="42738_20260130_120000")
        assert len(chain_analyses) == 2


class TestExportFunctions:
    """Tests for export functions."""

    def test_export_to_json(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Export analysis to submission-ready JSON."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        analysis = temp_db.get_analysis(analysis_id)
        data = export_to_json(analysis)

        assert data["review_request_id"] == 42738
        assert "body_top" in data
        assert "comments" in data
        assert len(data["comments"]) == 2
        assert data["comments"][0]["file_path"] == "src/main.c"
        assert data["comments"][0]["line_number"] == 42
        assert "text" in data["comments"][0]
        assert "metadata" in data
        assert data["metadata"]["analysis_id"] == analysis_id

    def test_export_to_json_empty_comments(self, temp_db: ReviewDatabase):
        """Export analysis with no comments."""
        result = ReviewResult(
            review_request_id=42738,
            diff_revision=1,
            comments=[],
            summary="No issues found",
        )
        analysis_id = temp_db.save_analysis(
            result=result,
            repository="test-repo",
            analysis_method="llm",
            model="claude",
        )

        analysis = temp_db.get_analysis(analysis_id)
        data = export_to_json(analysis)

        assert data["review_request_id"] == 42738
        assert len(data["comments"]) == 0
        assert data["ship_it"] is True  # No issues = ship it

    def test_export_to_markdown(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Export analysis to Markdown format."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        analysis = temp_db.get_analysis(analysis_id)
        markdown = export_to_markdown(analysis)

        # Check header
        assert "# Code Review: RR #42738" in markdown
        assert "**Repository**: test-repo" in markdown
        assert "**Diff Revision**: 1" in markdown
        assert "**Model**: claude-sonnet-4" in markdown
        assert "**Method**: llm" in markdown

        # Check summary
        assert "## Summary" in markdown
        assert "Found 2 issues" in markdown

        # Check comments
        assert "## Comments" in markdown
        assert "### src/main.c" in markdown
        assert "Line 42" in markdown
        assert "Potential null pointer dereference" in markdown
        assert "**Suggestion:**" in markdown

    def test_export_to_markdown_critical(
        self, temp_db: ReviewDatabase, sample_review_result_critical: ReviewResult
    ):
        """Export analysis with critical issues shows warning."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result_critical,
            repository="test-repo",
            analysis_method="llm",
            model="claude-sonnet-4",
        )

        analysis = temp_db.get_analysis(analysis_id)
        markdown = export_to_markdown(analysis)

        assert "**Warning**: Contains critical issues!" in markdown


class TestDatabaseComments:
    """Tests for comment storage and retrieval."""

    def test_comments_stored_correctly(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Comments are stored with all fields."""
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude",
        )

        analysis = temp_db.get_analysis(analysis_id)
        assert len(analysis.comments) == 2

        # Check first comment
        c1 = analysis.comments[0]
        assert c1.file_path == "src/main.c"
        assert c1.line_number == 42
        assert c1.message == "Potential null pointer dereference"
        assert c1.severity == "high"
        assert c1.issue_type == "bugs"
        assert c1.suggestion == "Add null check before use"

        # Check second comment
        c2 = analysis.comments[1]
        assert c2.file_path == "src/utils.c"
        assert c2.line_number == 15
        assert c2.severity == "medium"
        assert c2.issue_type == "security"

    def test_comments_cascade_delete(self, temp_db: ReviewDatabase, sample_review_result: ReviewResult):
        """Comments are deleted when analysis is removed."""
        # First, verify comments exist
        analysis_id = temp_db.save_analysis(
            result=sample_review_result,
            repository="test-repo",
            analysis_method="llm",
            model="claude",
        )

        analysis = temp_db.get_analysis(analysis_id)
        assert len(analysis.comments) == 2

        # Delete via cleanup (set old date)
        with temp_db._connection() as conn:
            conn.execute(
                "UPDATE analyses SET analyzed_at = ? WHERE id = ?",
                ((datetime.now() - timedelta(days=100)).isoformat(), analysis_id),
            )

        temp_db.cleanup(90)

        # Verify analysis and comments are gone
        assert temp_db.get_analysis(analysis_id) is None

        # Verify comments table is empty
        with temp_db._connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
            assert count == 0
