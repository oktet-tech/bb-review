"""Tests for Commenter and ReviewFormatter from bb_review/rr/rb_commenter.py."""

import pytest

from bb_review.models import ReviewComment, ReviewFocus, ReviewResult, Severity
from bb_review.rr.rb_commenter import Commenter, ReviewFormatter
from tests.mocks.rb_client import MockRBClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_comment(
    severity: Severity = Severity.MEDIUM,
    issue_type: ReviewFocus = ReviewFocus.BUGS,
    suggestion: str | None = None,
) -> ReviewComment:
    return ReviewComment(
        file_path="src/main.c",
        line_number=42,
        message="Something is wrong here",
        severity=severity,
        issue_type=issue_type,
        suggestion=suggestion,
    )


def _make_result(
    comments: list[ReviewComment] | None = None,
    has_critical: bool = False,
) -> ReviewResult:
    return ReviewResult(
        review_request_id=100,
        diff_revision=1,
        comments=comments or [],
        summary="Test review summary",
        has_critical_issues=has_critical,
    )


# ---------------------------------------------------------------------------
# Commenter.post_review
# ---------------------------------------------------------------------------


class TestCommenterPostReview:
    def test_posts_with_correct_fields(self):
        rb = MockRBClient()
        commenter = Commenter(rb)
        result = _make_result(comments=[_make_comment()])

        review_id = commenter.post_review(result)

        assert review_id is not None
        assert len(rb.posted_reviews) == 1
        posted = rb.posted_reviews[0]
        assert posted["review_request_id"] == 100
        assert len(posted["comments"]) == 1
        assert posted["ship_it"] is False

    def test_auto_ship_it_no_issues(self):
        rb = MockRBClient()
        commenter = Commenter(rb, auto_ship_it=True)
        result = _make_result(comments=[], has_critical=False)

        commenter.post_review(result)

        posted = rb.posted_reviews[0]
        assert posted["ship_it"] is True
        assert "Auto-approved" in posted["body_top"]

    def test_auto_ship_it_with_comments_no_ship(self):
        rb = MockRBClient()
        commenter = Commenter(rb, auto_ship_it=True)
        result = _make_result(comments=[_make_comment()])

        commenter.post_review(result)

        posted = rb.posted_reviews[0]
        assert posted["ship_it"] is False

    def test_auto_ship_it_with_critical_no_ship(self):
        rb = MockRBClient()
        commenter = Commenter(rb, auto_ship_it=True)
        result = _make_result(comments=[], has_critical=True)

        commenter.post_review(result)

        posted = rb.posted_reviews[0]
        assert posted["ship_it"] is False

    def test_dry_run_returns_none(self, capsys):
        rb = MockRBClient()
        commenter = Commenter(rb)
        result = _make_result(comments=[_make_comment()])

        ret = commenter.post_review(result, dry_run=True)

        assert ret is None
        assert len(rb.posted_reviews) == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out

    def test_api_error_re_raised(self):
        rb = MockRBClient()
        rb.post_review = lambda **kw: (_ for _ in ()).throw(RuntimeError("API down"))
        commenter = Commenter(rb)
        result = _make_result()

        with pytest.raises(RuntimeError, match="API down"):
            commenter.post_review(result)


# ---------------------------------------------------------------------------
# ReviewFormatter.format_for_submission
# ---------------------------------------------------------------------------


class TestFormatForSubmission:
    def test_all_fields_present(self):
        data = ReviewFormatter.format_for_submission(
            review_request_id=42,
            body_top="review body",
            comments=[{"file_path": "a.c", "line_number": 1, "text": "x"}],
            ship_it=True,
            unparsed_text="extra text",
            parsed_issues=[{"title": "Bug"}],
            metadata={"model": "gpt-4", "created_at": "2026-01-01"},
            rr_summary="Fix login",
        )
        assert data["review_request_id"] == 42
        assert data["body_top"] == "review body"
        assert len(data["comments"]) == 1
        assert data["ship_it"] is True
        assert data["unparsed_text"] == "extra text"
        assert data["parsed_issues"] == [{"title": "Bug"}]
        assert data["metadata"]["model"] == "gpt-4"
        assert data["rr_summary"] == "Fix login"

    def test_optional_rr_summary_missing(self):
        data = ReviewFormatter.format_for_submission(
            review_request_id=1,
            body_top="body",
            comments=[],
        )
        assert "rr_summary" not in data

    def test_optional_parsed_issues_missing(self):
        data = ReviewFormatter.format_for_submission(
            review_request_id=1,
            body_top="body",
            comments=[],
        )
        assert "parsed_issues" not in data

    def test_default_metadata(self):
        data = ReviewFormatter.format_for_submission(
            review_request_id=1,
            body_top="body",
            comments=[],
        )
        assert "created_at" in data["metadata"]
        assert data["metadata"]["dry_run"] is True


# ---------------------------------------------------------------------------
# ReviewFormatter.format_as_markdown
# ---------------------------------------------------------------------------


class TestFormatAsMarkdown:
    def test_critical_issues_warning(self):
        result = _make_result(
            comments=[_make_comment(Severity.CRITICAL)],
            has_critical=True,
        )
        md = ReviewFormatter.format_as_markdown(result)
        assert "Warning" in md or "Critical" in md

    def test_multiple_files_grouped(self):
        c1 = ReviewComment(
            file_path="a.c",
            line_number=1,
            message="issue1",
            severity=Severity.HIGH,
            issue_type=ReviewFocus.BUGS,
        )
        c2 = ReviewComment(
            file_path="b.c",
            line_number=5,
            message="issue2",
            severity=Severity.LOW,
            issue_type=ReviewFocus.STYLE,
        )
        result = _make_result(comments=[c1, c2])
        md = ReviewFormatter.format_as_markdown(result)
        assert "`a.c`" in md
        assert "`b.c`" in md

    def test_no_issues(self):
        result = _make_result(comments=[])
        md = ReviewFormatter.format_as_markdown(result)
        assert "No issues found" in md


# ---------------------------------------------------------------------------
# ReviewFormatter.format_as_json
# ---------------------------------------------------------------------------


class TestFormatAsJson:
    def test_all_fields_serialized(self):
        result = _make_result(
            comments=[_make_comment(suggestion="fix this")],
        )
        data = ReviewFormatter.format_as_json(result)
        assert data["review_request_id"] == 100
        assert data["diff_revision"] == 1
        assert "analyzed_at" in data
        assert data["summary"] == "Test review summary"
        assert data["has_critical_issues"] is False
        assert data["issue_count"] == 1
        c = data["comments"][0]
        assert c["file_path"] == "src/main.c"
        assert c["line_number"] == 42
        assert c["severity"] == "medium"
        assert c["issue_type"] == "bugs"
        assert c["suggestion"] == "fix this"
