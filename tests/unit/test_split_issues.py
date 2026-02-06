"""Tests for _split_issues_by_rr from bb_review/cli/_review_runner.py."""

from bb_review.cli._review_runner import _split_issues_by_rr
from bb_review.reviewers.opencode import ParsedIssue


def _issue(file_path=None, title="test issue"):
    return ParsedIssue(title=title, file_path=file_path)


class TestSplitIssuesByRR:
    """Tests for splitting issues across review requests."""

    def test_exact_match(self):
        file_to_rr = {"src/a.c": 100, "src/b.c": 200}
        issues = [_issue("src/a.c", "issue A"), _issue("src/b.c", "issue B")]

        result = _split_issues_by_rr(issues, file_to_rr, fallback_rr_id=200)

        assert len(result[100]) == 1
        assert result[100][0].title == "issue A"
        assert len(result[200]) == 1
        assert result[200][0].title == "issue B"

    def test_suffix_match_llm_shorter_path(self):
        """LLM returns 'b.c' but mapping has 'src/b.c'."""
        file_to_rr = {"src/a.c": 100, "src/b.c": 200}
        issues = [_issue("b.c", "issue B")]

        result = _split_issues_by_rr(issues, file_to_rr, fallback_rr_id=100)

        assert len(result[200]) == 1

    def test_suffix_match_llm_longer_path(self):
        """LLM returns 'repo/src/a.c' but mapping has 'src/a.c'."""
        file_to_rr = {"src/a.c": 100}
        issues = [_issue("repo/src/a.c", "issue A")]

        result = _split_issues_by_rr(issues, file_to_rr, fallback_rr_id=999)

        assert len(result[100]) == 1

    def test_general_issue_no_file(self):
        """Issues without file_path go to fallback (tip) RR."""
        file_to_rr = {"src/a.c": 100}
        issues = [_issue(None, "general")]

        result = _split_issues_by_rr(issues, file_to_rr, fallback_rr_id=200)

        assert len(result[200]) == 1
        assert result[200][0].title == "general"

    def test_unknown_file_falls_back(self):
        """Files not in any RR's diff go to fallback."""
        file_to_rr = {"src/a.c": 100}
        issues = [_issue("src/unknown.c", "unknown")]

        result = _split_issues_by_rr(issues, file_to_rr, fallback_rr_id=200)

        assert len(result[200]) == 1

    def test_all_same_rr(self):
        """All issues map to one RR -- single entry in result."""
        file_to_rr = {"src/a.c": 100, "src/b.c": 100}
        issues = [_issue("src/a.c"), _issue("src/b.c"), _issue(None)]

        result = _split_issues_by_rr(issues, file_to_rr, fallback_rr_id=100)

        assert list(result.keys()) == [100]
        assert len(result[100]) == 3

    def test_empty_issues(self):
        result = _split_issues_by_rr([], {"src/a.c": 100}, fallback_rr_id=100)
        assert result == {}

    def test_empty_file_to_rr(self):
        """No file mapping -> everything falls back to tip."""
        issues = [_issue("src/a.c"), _issue(None)]

        result = _split_issues_by_rr(issues, {}, fallback_rr_id=999)

        assert list(result.keys()) == [999]
        assert len(result[999]) == 2
