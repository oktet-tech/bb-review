"""Integration tests for the LLM Analyzer."""

import pytest

from bb_review.models import ReviewFocus, ReviewGuidelines, Severity
from bb_review.reviewers.llm import Analyzer


class TestAnalyzerIntegration:
    """Integration tests for Analyzer with mock LLM."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider fixture."""
        from tests.mocks import MockLLMProvider

        return MockLLMProvider()

    @pytest.fixture
    def analyzer_with_mock(self, mock_provider):
        """Create analyzer with mocked provider."""
        analyzer = Analyzer(
            api_key="test-key",
            model="test-model",
            provider="anthropic",
        )
        analyzer.llm = mock_provider
        return analyzer, mock_provider

    def test_analyze_with_mock_llm(self, analyzer_with_mock, sample_diff: str):
        """Full pipeline with mock provider."""
        analyzer, mock = analyzer_with_mock

        mock.set_response(
            {
                "summary": "Found 1 issue",
                "has_critical_issues": False,
                "comments": [
                    {
                        "file_path": "src/main.c",
                        "line_number": 12,
                        "severity": "low",
                        "issue_type": "style",
                        "message": "Test issue",
                    }
                ],
            }
        )

        result = analyzer.analyze(
            diff=sample_diff,
            guidelines=ReviewGuidelines.default(),
            review_request_id=42738,
            diff_revision=1,
        )

        assert result.review_request_id == 42738
        assert result.diff_revision == 1
        assert result.summary == "Found 1 issue"
        assert len(result.comments) == 1

    def test_analyze_extracts_comments(self, analyzer_with_mock, sample_response: dict):
        """Comments parsed from response."""
        analyzer, mock = analyzer_with_mock
        mock.set_response(sample_response)

        result = analyzer.analyze(
            diff="test diff",
            guidelines=ReviewGuidelines.default(),
            review_request_id=1,
            diff_revision=1,
        )

        assert len(result.comments) == 2
        assert result.comments[0].file_path == "src/main.c"
        assert result.comments[0].line_number == 12
        assert result.comments[0].severity == Severity.LOW
        assert result.comments[1].file_path == "src/utils.c"
        assert result.comments[1].severity == Severity.MEDIUM

    def test_analyze_handles_api_error(self, analyzer_with_mock):
        """Graceful handling of API errors."""
        from tests.mocks.llm_provider import MockLLMProviderError

        analyzer, _ = analyzer_with_mock
        analyzer.llm = MockLLMProviderError(RuntimeError("API error"))

        with pytest.raises(RuntimeError, match="API error"):
            analyzer.analyze(
                diff="test",
                guidelines=ReviewGuidelines.default(),
                review_request_id=1,
                diff_revision=1,
            )

    def test_get_last_raw_response(self, analyzer_with_mock):
        """Raw response stored for debugging."""
        analyzer, mock = analyzer_with_mock

        response = {"summary": "Test", "has_critical_issues": False, "comments": []}
        mock.set_response(response)

        analyzer.analyze(
            diff="test",
            guidelines=ReviewGuidelines.default(),
            review_request_id=1,
            diff_revision=1,
        )

        raw = analyzer.get_last_raw_response()
        assert raw is not None
        assert "Test" in raw

    def test_analyze_with_file_contexts(self, analyzer_with_mock):
        """Analysis includes file contexts in prompt."""
        analyzer, mock = analyzer_with_mock

        file_contexts = {
            "src/main.c": "int main() { return 0; }",
            "src/utils.c": "void helper() {}",
        }

        analyzer.analyze(
            diff="test diff",
            guidelines=ReviewGuidelines.default(),
            file_contexts=file_contexts,
            review_request_id=1,
            diff_revision=1,
        )

        # Check that contexts were included in prompt
        call = mock.get_last_call()
        assert "src/main.c" in call["user"]
        assert "src/utils.c" in call["user"]
        assert "int main()" in call["user"]

    def test_analyze_with_custom_guidelines(self, analyzer_with_mock):
        """Analysis uses custom guidelines."""
        analyzer, mock = analyzer_with_mock

        guidelines = ReviewGuidelines(
            focus=[ReviewFocus.SECURITY, ReviewFocus.PERFORMANCE],
            context="Embedded C project",
            custom_rules=["Check for buffer overflows", "Verify interrupt safety"],
            severity_threshold=Severity.HIGH,
        )

        analyzer.analyze(
            diff="test diff",
            guidelines=guidelines,
            review_request_id=1,
            diff_revision=1,
        )

        call = mock.get_last_call()
        assert "security" in call["user"].lower()
        assert "performance" in call["user"].lower()
        assert "Embedded C project" in call["user"]
        assert "buffer overflows" in call["user"]
        assert "high" in call["user"].lower()

    def test_analyze_empty_response(self, analyzer_with_mock):
        """Handle empty LLM response."""
        analyzer, mock = analyzer_with_mock
        mock.set_response("")

        result = analyzer.analyze(
            diff="test",
            guidelines=ReviewGuidelines.default(),
            review_request_id=1,
            diff_revision=1,
        )

        # Should return result with error summary
        assert "Failed to parse" in result.summary
        assert len(result.comments) == 0

    def test_analyze_preserves_request_info(self, analyzer_with_mock):
        """Review request ID and diff revision are preserved."""
        analyzer, mock = analyzer_with_mock

        result = analyzer.analyze(
            diff="test",
            guidelines=ReviewGuidelines.default(),
            review_request_id=99999,
            diff_revision=5,
        )

        assert result.review_request_id == 99999
        assert result.diff_revision == 5


class TestReviewFormatterMethods:
    """Tests for ReviewFormatter formatting methods (moved from Analyzer)."""

    def test_format_comment_text(self):
        """Format review comment for posting."""
        from bb_review.models import ReviewComment
        from bb_review.rr.rb_commenter import ReviewFormatter

        comment = ReviewComment(
            file_path="test.c",
            line_number=10,
            message="This is a bug",
            severity=Severity.HIGH,
            issue_type=ReviewFocus.BUGS,
            suggestion="Fix it like this",
        )

        formatted = ReviewFormatter.format_comment_text(comment)

        assert "HIGH" in formatted
        assert "bugs" in formatted
        assert "This is a bug" in formatted
        assert "Fix it like this" in formatted

    def test_format_review_summary_no_issues(self):
        """Format summary when no issues found."""
        from bb_review.models import ReviewResult
        from bb_review.rr.rb_commenter import ReviewFormatter

        result = ReviewResult(
            review_request_id=1,
            diff_revision=1,
            comments=[],
            summary="Code looks good",
        )

        formatted = ReviewFormatter.format_review_summary(result)

        assert "No issues found" in formatted
        assert "Code looks good" in formatted

    def test_format_review_summary_with_issues(self):
        """Format summary when issues found."""
        from bb_review.models import ReviewComment, ReviewResult
        from bb_review.rr.rb_commenter import ReviewFormatter

        result = ReviewResult(
            review_request_id=1,
            diff_revision=1,
            comments=[
                ReviewComment(
                    file_path="test.c",
                    line_number=1,
                    message="Issue 1",
                    severity=Severity.HIGH,
                    issue_type=ReviewFocus.BUGS,
                ),
                ReviewComment(
                    file_path="test.c",
                    line_number=2,
                    message="Issue 2",
                    severity=Severity.MEDIUM,
                    issue_type=ReviewFocus.STYLE,
                ),
            ],
            summary="Found issues",
        )

        formatted = ReviewFormatter.format_review_summary(result)

        assert "High: 1" in formatted
        assert "Medium: 1" in formatted

    def test_format_review_summary_critical(self):
        """Format summary with critical issues warning."""
        from bb_review.models import ReviewComment, ReviewResult
        from bb_review.rr.rb_commenter import ReviewFormatter

        result = ReviewResult(
            review_request_id=1,
            diff_revision=1,
            comments=[
                ReviewComment(
                    file_path="test.c",
                    line_number=1,
                    message="Critical issue",
                    severity=Severity.CRITICAL,
                    issue_type=ReviewFocus.SECURITY,
                ),
            ],
            summary="Critical security issue",
            has_critical_issues=True,
        )

        formatted = ReviewFormatter.format_review_summary(result)

        assert "Critical" in formatted
        assert "Please address before merging" in formatted
