"""Tests for dedup and 3-state comment status in the TUI."""

from datetime import datetime

from bb_review.db.models import StoredComment
from bb_review.models import ReviewComment, ReviewFocus, ReviewResult, Severity
from bb_review.rr.dedup import (
    DroppedComment,
    _extract_message_core,
    fetch_dropped_comments,
    filter_dropped,
)
from bb_review.ui.models import CommentStatus, ExportableAnalysis, SelectableComment


# -- Mock RB client --


class _MockClient:
    def __init__(self, reviews=None, diff_comments=None, filediff_cache=None):
        self._reviews = reviews or []
        self._diff_comments = diff_comments or {}
        self._filediff_cache = filediff_cache or {}

    def get_reviews(self, rr_id):
        return self._reviews

    def get_review_diff_comments(self, rr_id, review_id):
        return self._diff_comments.get(review_id, [])

    def _warm_filediff_cache(self, rr_id):
        self._filediff_cache.setdefault(rr_id, [])


def _review(review_id, username):
    return {
        "id": review_id,
        "links": {"user": {"href": f"/api/users/{username}/"}},
    }


def _diff_comment(comment_id, text, issue_status="dropped", filediff_id=1):
    return {
        "id": comment_id,
        "text": text,
        "issue_status": issue_status,
        "links": {
            "filediff": {"href": f"/api/filediffs/{filediff_id}/"},
        },
    }


def _make_result(comments):
    return ReviewResult(
        review_request_id=100,
        diff_revision=2,
        comments=comments,
        summary="Test review",
        has_critical_issues=False,
        analyzed_at=datetime(2026, 1, 1),
    )


def _make_comment(file_path, message, severity=Severity.MEDIUM):
    return ReviewComment(
        file_path=file_path,
        line_number=10,
        message=message,
        severity=severity,
        issue_type=ReviewFocus.BUGS,
    )


# ---------------------------------------------------------------------------
# _extract_message_core
# ---------------------------------------------------------------------------


class TestExtractMessageCore:
    def test_strips_header_and_suggestion(self):
        text = (
            "[WARNING] **MEDIUM** (bugs)\n"
            "\n"
            "Variable x is used before assignment.\n"
            "\n"
            "**Suggestion:**\n"
            "Initialize x before the loop."
        )
        assert _extract_message_core(text) == "Variable x is used before assignment."

    def test_strips_header_only(self):
        text = "[CRITICAL] **CRITICAL** (security)\n\nSQL injection via user input."
        assert _extract_message_core(text) == "SQL injection via user input."

    def test_plain_text_unchanged(self):
        text = "This is just a plain comment."
        assert _extract_message_core(text) == text

    def test_multiline_suggestion_stripped(self):
        text = (
            "[HIGH] **HIGH** (performance)\n"
            "\n"
            "N+1 query in the loop.\n"
            "\n"
            "**Suggestion:**\n"
            "Use batch loading.\n"
            "Or use a join query."
        )
        assert _extract_message_core(text) == "N+1 query in the loop."


# ---------------------------------------------------------------------------
# filter_dropped
# ---------------------------------------------------------------------------


class TestFilterDropped:
    def test_no_dropped_comments(self):
        comment = _make_comment("src/main.py", "Some issue")
        result = _make_result([comment])

        filtered, removed = filter_dropped(result, [])
        assert len(filtered.comments) == 1
        assert len(removed) == 0

    def test_exact_match_filtered(self):
        comment = _make_comment("src/main.py", "Variable x is used before assignment.")
        result = _make_result([comment])

        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]

        filtered, removed = filter_dropped(result, dropped)
        assert len(filtered.comments) == 0
        assert len(removed) == 1

    def test_fuzzy_match_filtered(self):
        comment = _make_comment("src/main.py", "Variable x is used before it is assigned.")
        result = _make_result([comment])

        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]

        filtered, removed = filter_dropped(result, dropped, threshold=0.6)
        assert len(filtered.comments) == 0
        assert len(removed) == 1

    def test_different_file_not_filtered(self):
        comment = _make_comment("src/main.py", "Variable x is used before assignment.")
        result = _make_result([comment])

        dropped = [DroppedComment(file_path="src/other.py", text="Variable x is used before assignment.")]

        filtered, removed = filter_dropped(result, dropped)
        assert len(filtered.comments) == 1
        assert len(removed) == 0

    def test_low_similarity_not_filtered(self):
        comment = _make_comment("src/main.py", "Completely different issue about logging.")
        result = _make_result([comment])

        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]

        filtered, removed = filter_dropped(result, dropped)
        assert len(filtered.comments) == 1
        assert len(removed) == 0

    def test_mixed_keep_and_remove(self):
        c1 = _make_comment("src/main.py", "Variable x is used before assignment.")
        c2 = _make_comment("src/main.py", "Logging is missing in error handler.")
        result = _make_result([c1, c2])

        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]

        filtered, removed = filter_dropped(result, dropped)
        assert len(filtered.comments) == 1
        assert filtered.comments[0].message == "Logging is missing in error handler."
        assert len(removed) == 1

    def test_has_critical_issues_updated(self):
        c1 = _make_comment("src/main.py", "SQL injection", severity=Severity.CRITICAL)
        c2 = _make_comment("src/main.py", "Minor style issue", severity=Severity.LOW)
        result = _make_result([c1, c2])
        result.has_critical_issues = True

        dropped = [DroppedComment(file_path="src/main.py", text="SQL injection")]

        filtered, _ = filter_dropped(result, dropped)
        assert not filtered.has_critical_issues


# ---------------------------------------------------------------------------
# fetch_dropped_comments
# ---------------------------------------------------------------------------


class TestFetchDroppedComments:
    def test_fetches_bot_dropped_comments(self):
        client = _MockClient(
            reviews=[_review(1, "bot"), _review(2, "human")],
            diff_comments={
                1: [
                    _diff_comment(10, "[WARNING] **MEDIUM** (bugs)\n\nBad variable name.", "dropped"),
                    _diff_comment(11, "[HIGH] **HIGH** (bugs)\n\nNull deref.", "open"),
                ],
                2: [
                    _diff_comment(20, "Human comment", "dropped"),
                ],
            },
            filediff_cache={
                100: [{"id": 1, "dest_file": "src/foo.py", "source_file": "src/foo.py"}],
            },
        )

        dropped = fetch_dropped_comments(client, 100, "bot")
        assert len(dropped) == 1
        assert dropped[0].file_path == "src/foo.py"
        assert dropped[0].text == "Bad variable name."

    def test_no_dropped_returns_empty(self):
        client = _MockClient(
            reviews=[_review(1, "bot")],
            diff_comments={
                1: [_diff_comment(10, "Some comment", "open")],
            },
            filediff_cache={100: [{"id": 1, "dest_file": "src/foo.py"}]},
        )

        dropped = fetch_dropped_comments(client, 100, "bot")
        assert len(dropped) == 0

    def test_no_reviews_returns_empty(self):
        client = _MockClient()
        dropped = fetch_dropped_comments(client, 100, "bot")
        assert len(dropped) == 0


# ---------------------------------------------------------------------------
# CommentStatus toggle
# ---------------------------------------------------------------------------


def _stored_comment(file_path="src/main.py", message="Some issue"):
    return StoredComment(
        id=1,
        analysis_id=1,
        file_path=file_path,
        line_number=10,
        message=message,
        severity="medium",
        issue_type="bugs",
    )


class TestCommentStatusToggle:
    def test_included_to_excluded(self):
        sc = SelectableComment(comment=_stored_comment())
        assert sc.status == CommentStatus.INCLUDED
        sc.toggle()
        assert sc.status == CommentStatus.EXCLUDED

    def test_excluded_to_included(self):
        sc = SelectableComment(comment=_stored_comment(), status=CommentStatus.EXCLUDED)
        sc.toggle()
        assert sc.status == CommentStatus.INCLUDED

    def test_duplicate_to_included(self):
        sc = SelectableComment(comment=_stored_comment(), status=CommentStatus.DUPLICATE)
        sc.toggle()
        assert sc.status == CommentStatus.INCLUDED

    def test_full_cycle_from_duplicate(self):
        """[-] -> [x] -> [ ] -> [x]"""
        sc = SelectableComment(comment=_stored_comment(), status=CommentStatus.DUPLICATE)
        sc.toggle()
        assert sc.status == CommentStatus.INCLUDED
        sc.toggle()
        assert sc.status == CommentStatus.EXCLUDED
        sc.toggle()
        assert sc.status == CommentStatus.INCLUDED

    def test_selected_property(self):
        sc = SelectableComment(comment=_stored_comment())
        assert sc.selected is True
        assert sc.is_submittable is True

        sc.status = CommentStatus.DUPLICATE
        assert sc.selected is False
        assert sc.is_submittable is False

        sc.status = CommentStatus.EXCLUDED
        assert sc.selected is False


# ---------------------------------------------------------------------------
# ExportableAnalysis.mark_duplicates
# ---------------------------------------------------------------------------


class TestMarkDuplicates:
    def _make_ea(self, messages):
        from bb_review.db.models import AnalysisMethod, AnalysisStatus, StoredAnalysis

        analysis = StoredAnalysis(
            id=1,
            review_request_id=100,
            repository="test",
            diff_revision=1,
            summary="Test",
            has_critical_issues=False,
            model_used="test",
            analysis_method=AnalysisMethod.LLM,
            status=AnalysisStatus.DRAFT,
            analyzed_at=datetime(2026, 1, 1),
            comments=[
                StoredComment(
                    id=i,
                    analysis_id=1,
                    file_path="src/main.py",
                    line_number=10 + i,
                    message=msg,
                    severity="medium",
                    issue_type="bugs",
                )
                for i, msg in enumerate(messages)
            ],
        )
        return ExportableAnalysis.from_stored(analysis)

    def test_exact_match_marks_duplicate(self):
        ea = self._make_ea(["Variable x is used before assignment.", "Unrelated issue."])
        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]

        ea.mark_duplicates(dropped)

        assert ea.comments[0].status == CommentStatus.DUPLICATE
        assert ea.comments[1].status == CommentStatus.INCLUDED

    def test_fuzzy_match_marks_duplicate(self):
        ea = self._make_ea(["Variable x is used before it is assigned."])
        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]

        ea.mark_duplicates(dropped, threshold=0.6)
        assert ea.comments[0].status == CommentStatus.DUPLICATE

    def test_different_file_not_marked(self):
        ea = self._make_ea(["Variable x is used before assignment."])
        dropped = [DroppedComment(file_path="src/other.py", text="Variable x is used before assignment.")]

        ea.mark_duplicates(dropped)
        assert ea.comments[0].status == CommentStatus.INCLUDED

    def test_empty_dropped_no_change(self):
        ea = self._make_ea(["Some issue."])
        ea.mark_duplicates([])
        assert ea.comments[0].status == CommentStatus.INCLUDED

    def test_selected_comments_excludes_duplicates(self):
        ea = self._make_ea(
            [
                "Variable x is used before assignment.",
                "Completely unrelated logging issue in the handler.",
            ]
        )
        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]
        ea.mark_duplicates(dropped)

        assert ea.selected_count == 1
        assert ea.duplicate_count == 1
        assert ea.selected_comments[0].comment.message == "Completely unrelated logging issue in the handler."

    def test_does_not_override_excluded(self):
        """If a comment is already excluded by user, mark_duplicates should not touch it."""
        ea = self._make_ea(["Variable x is used before assignment."])
        ea.comments[0].status = CommentStatus.EXCLUDED

        dropped = [DroppedComment(file_path="src/main.py", text="Variable x is used before assignment.")]
        ea.mark_duplicates(dropped)

        # Should remain excluded, not become duplicate
        assert ea.comments[0].status == CommentStatus.EXCLUDED
