"""Tests for LLM response parsing and prompt building."""

from bb_review.models import ReviewFocus, ReviewGuidelines, Severity
from bb_review.reviewers.llm import Analyzer, extract_changed_files, filter_diff_by_paths


class TestLLMResponseParsing:
    """Tests for parsing LLM responses."""

    def test_parse_valid_json(self, sample_response: dict):
        """Parse well-formed JSON response."""
        # Create analyzer with mock provider
        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider(sample_response)

        # Patch create_provider to return our mock
        import bb_review.reviewers.llm as llm_module

        original_create = llm_module.create_provider

        def mock_create(*args, **kwargs):
            return mock

        llm_module.create_provider = mock_create

        try:
            analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
            # Override the llm directly
            analyzer.llm = mock

            result = analyzer.analyze(
                diff="test diff",
                guidelines=ReviewGuidelines.default(),
                review_request_id=1,
                diff_revision=1,
            )

            assert result.summary == "Found 2 issues in the code change"
            assert len(result.comments) == 2
            assert result.comments[0].file_path == "src/main.c"
            assert result.comments[0].line_number == 12
            assert result.comments[0].severity == Severity.LOW
        finally:
            llm_module.create_provider = original_create

    def test_parse_json_in_markdown(self):
        """Extract JSON from markdown code block."""
        from bb_review.reviewers.llm import Analyzer

        # Response wrapped in markdown
        response_text = """Here's my analysis:

```json
{
  "summary": "Test",
  "has_critical_issues": false,
  "comments": []
}
```

That's all."""

        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider(response_text)

        analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
        analyzer.llm = mock

        result = analyzer.analyze(
            diff="test",
            guidelines=ReviewGuidelines.default(),
            review_request_id=1,
            diff_revision=1,
        )

        assert result.summary == "Test"
        assert len(result.comments) == 0

    def test_parse_empty_comments(self):
        """Handle empty comments array."""
        response = {
            "summary": "No issues found",
            "has_critical_issues": False,
            "comments": [],
        }

        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider(response)

        analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
        analyzer.llm = mock

        result = analyzer.analyze(
            diff="test",
            guidelines=ReviewGuidelines.default(),
            review_request_id=1,
            diff_revision=1,
        )

        assert result.issue_count == 0

    def test_parse_invalid_json(self):
        """Return empty result on bad JSON."""
        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider("This is not JSON at all")

        analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
        analyzer.llm = mock

        result = analyzer.analyze(
            diff="test",
            guidelines=ReviewGuidelines.default(),
            review_request_id=1,
            diff_revision=1,
        )

        assert result.summary == "Failed to parse review response"
        assert len(result.comments) == 0

    def test_parse_missing_fields(self):
        """Skip comments with missing required fields."""
        response = {
            "summary": "Test",
            "has_critical_issues": False,
            "comments": [
                {
                    "file_path": "test.c",
                    "line_number": 1,
                    "message": "Valid comment",
                },
                {
                    # Missing file_path
                    "line_number": 2,
                    "message": "Invalid comment",
                },
                {
                    "file_path": "test.c",
                    # Missing line_number
                    "message": "Also invalid",
                },
            ],
        }

        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider(response)

        analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
        analyzer.llm = mock

        result = analyzer.analyze(
            diff="test",
            guidelines=ReviewGuidelines.default(),
            review_request_id=1,
            diff_revision=1,
        )

        # Only the valid comment should be included
        assert len(result.comments) == 1
        assert result.comments[0].file_path == "test.c"


class TestPromptBuilding:
    """Tests for prompt building."""

    def test_build_prompt_with_context(self):
        """Build prompt with file contexts."""
        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider()

        analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
        analyzer.llm = mock

        guidelines = ReviewGuidelines.default()

        file_contexts = {
            "src/main.c": "int main() { return 0; }",
            "src/utils.c": "void helper() {}",
        }

        analyzer.analyze(
            diff="test diff",
            guidelines=guidelines,
            file_contexts=file_contexts,
            review_request_id=1,
            diff_revision=1,
        )

        # Check the prompt contains file context
        assert mock.get_call_count() == 1
        call = mock.get_last_call()
        assert "src/main.c" in call["user"]
        assert "src/utils.c" in call["user"]

    def test_build_prompt_with_rules(self):
        """Build prompt with custom rules."""
        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider()

        analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
        analyzer.llm = mock

        guidelines = ReviewGuidelines(
            focus=[ReviewFocus.BUGS, ReviewFocus.SECURITY],
            custom_rules=["Always check for NULL pointers", "Use safe string functions"],
        )

        analyzer.analyze(
            diff="test diff",
            guidelines=guidelines,
            review_request_id=1,
            diff_revision=1,
        )

        call = mock.get_last_call()
        assert "Always check for NULL pointers" in call["user"]
        assert "Use safe string functions" in call["user"]

    def test_build_prompt_with_ignore_paths(self):
        """Build prompt mentioning ignore paths."""
        from tests.mocks import MockLLMProvider

        mock = MockLLMProvider()

        analyzer = Analyzer(api_key="test", model="test", provider="anthropic")
        analyzer.llm = mock

        guidelines = ReviewGuidelines(
            ignore_paths=["*.test.c", "tests/*"],
        )

        analyzer.analyze(
            diff="test diff",
            guidelines=guidelines,
            review_request_id=1,
            diff_revision=1,
        )

        call = mock.get_last_call()
        assert "*.test.c" in call["user"]
        assert "tests/*" in call["user"]


class TestExtractChangedFiles:
    """Tests for extract_changed_files function."""

    def test_extract_changed_files(self, sample_diff: str):
        """Extract file paths and lines from diff."""
        files = extract_changed_files(sample_diff)

        assert len(files) == 2

        # First file (modified)
        assert files[0]["path"] == "src/main.c"
        assert len(files[0]["lines"]) > 0

        # Second file (new)
        assert files[1]["path"] == "src/utils.c"

    def test_extract_empty_diff(self):
        """Handle empty diff."""
        files = extract_changed_files("")
        assert files == []

    def test_extract_single_file(self):
        """Extract single file change."""
        diff = """diff --git a/file.txt b/file.txt
index abc..def 100644
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,4 @@
 line1
+new line
 line2
"""
        files = extract_changed_files(diff)

        assert len(files) == 1
        assert files[0]["path"] == "file.txt"


class TestFilterDiffByPaths:
    """Tests for filter_diff_by_paths function."""

    def test_filter_ignored_paths(self, sample_diff: str):
        """Remove files matching ignore patterns."""
        filtered = filter_diff_by_paths(sample_diff, ["src/utils.c"])

        assert "src/main.c" in filtered
        assert "src/utils.c" not in filtered

    def test_filter_with_glob_pattern(self, sample_diff: str):
        """Filter using glob patterns."""
        filtered = filter_diff_by_paths(sample_diff, ["*.c"])

        # All .c files should be removed
        assert "src/main.c" not in filtered
        assert "src/utils.c" not in filtered

    def test_filter_no_match(self, sample_diff: str):
        """Keep files when no patterns match."""
        filtered = filter_diff_by_paths(sample_diff, ["*.py"])

        # Nothing should be filtered
        assert "src/main.c" in filtered
        assert "src/utils.c" in filtered

    def test_filter_empty_patterns(self, sample_diff: str):
        """Empty patterns filter nothing."""
        filtered = filter_diff_by_paths(sample_diff, [])

        assert "src/main.c" in filtered
        assert "src/utils.c" in filtered
