"""Tests for TriageAnalyzer from bb_review/triage/analyzer.py."""

from bb_review.triage.analyzer import TriageAnalyzer
from bb_review.triage.models import (
    CommentClassification,
    Difficulty,
    RBComment,
)
from tests.mocks.llm_provider import MockLLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _comment(comment_id=1, text="Fix this", file_path="src/main.c", line=42):
    return RBComment(
        review_id=100,
        comment_id=comment_id,
        reviewer="alice",
        text=text,
        file_path=file_path,
        line_number=line,
    )


def _triage_response(comments):
    """Build a mock LLM triage response."""
    return {
        "summary": "Test triage",
        "comments": comments,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTriageAnalyzerParsing:
    def test_parses_valid_response(self):
        response = _triage_response(
            [
                {
                    "comment_id": 1,
                    "classification": "valid",
                    "difficulty": "simple",
                    "fix_hint": "Add null check",
                    "reply_suggestion": "Good catch",
                },
            ]
        )
        provider = MockLLMProvider(response)
        analyzer = TriageAnalyzer(provider=provider)

        result = analyzer.analyze([_comment()], diff="some diff")

        assert len(result.triaged_comments) == 1
        t = result.triaged_comments[0]
        assert t.classification == CommentClassification.VALID
        assert t.difficulty == Difficulty.SIMPLE
        assert t.fix_hint == "Add null check"
        assert t.reply_suggestion == "Good catch"

    def test_unknown_classification_defaults_to_valid(self):
        response = _triage_response(
            [
                {"comment_id": 1, "classification": "unknown_type"},
            ]
        )
        provider = MockLLMProvider(response)
        analyzer = TriageAnalyzer(provider=provider)

        result = analyzer.analyze([_comment()], diff="diff")
        assert result.triaged_comments[0].classification == CommentClassification.VALID

    def test_missing_comments_get_default_classification(self):
        """Comments the LLM doesn't mention are classified as valid."""
        response = _triage_response(
            [
                {"comment_id": 1, "classification": "nitpick"},
            ]
        )
        provider = MockLLMProvider(response)
        analyzer = TriageAnalyzer(provider=provider)

        comments = [_comment(comment_id=1), _comment(comment_id=2, text="Other")]
        result = analyzer.analyze(comments, diff="diff")

        assert len(result.triaged_comments) == 2
        by_id = {t.source.comment_id: t for t in result.triaged_comments}
        assert by_id[1].classification == CommentClassification.NITPICK
        assert by_id[2].classification == CommentClassification.VALID

    def test_invalid_json_returns_fallback(self):
        provider = MockLLMProvider("not valid json at all")
        analyzer = TriageAnalyzer(provider=provider)

        result = analyzer.analyze([_comment()], diff="diff")
        assert len(result.triaged_comments) == 1
        assert result.triaged_comments[0].classification == CommentClassification.VALID

    def test_empty_comments_returns_empty(self):
        provider = MockLLMProvider()
        analyzer = TriageAnalyzer(provider=provider)

        result = analyzer.analyze([], diff="diff")
        assert result.triaged_comments == []

    def test_all_classifications_parsed(self):
        items = []
        for i, cls in enumerate(CommentClassification):
            items.append(
                {
                    "comment_id": i + 1,
                    "classification": cls.value,
                }
            )
        response = _triage_response(items)
        provider = MockLLMProvider(response)
        analyzer = TriageAnalyzer(provider=provider)

        comments = [_comment(comment_id=i + 1) for i in range(len(CommentClassification))]
        result = analyzer.analyze(comments, diff="diff")

        classifications = {t.classification for t in result.triaged_comments}
        assert classifications == set(CommentClassification)


class TestTriageAnalyzerPrompt:
    def test_prompt_includes_comment_text(self):
        provider = MockLLMProvider()
        analyzer = TriageAnalyzer(provider=provider)

        analyzer.analyze([_comment(text="Check null ptr")], diff="test diff")

        call = provider.get_last_call()
        assert "Check null ptr" in call["user"]
        assert "test diff" in call["user"]

    def test_prompt_includes_guidelines(self):
        provider = MockLLMProvider()
        analyzer = TriageAnalyzer(provider=provider)

        analyzer.analyze(
            [_comment()],
            diff="diff",
            guidelines_text="Always check return values",
        )

        call = provider.get_last_call()
        assert "Always check return values" in call["user"]
