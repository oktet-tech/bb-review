"""Tests for RBCommentFetcher from bb_review/rr/rb_fetcher.py."""

from bb_review.rr.rb_fetcher import RBCommentFetcher


# -- Minimal mock that satisfies RBCommentFetcher interface --


class _MockClient:
    def __init__(self, reviews=None, diff_comments=None, replies=None):
        self._reviews = reviews or []
        self._diff_comments = diff_comments or {}
        self._replies = replies or {}
        self._filediff_cache = {}

    def get_reviews(self, rr_id):
        return self._reviews

    def get_review_diff_comments(self, rr_id, review_id):
        return self._diff_comments.get(review_id, [])

    def get_review_replies(self, rr_id, review_id):
        return self._replies.get(review_id, [])

    def _warm_filediff_cache(self, rr_id):
        self._filediff_cache.setdefault(rr_id, [])


def _review(review_id, username, body_top=""):
    return {
        "id": review_id,
        "body_top": body_top,
        "links": {"user": {"href": f"/api/users/{username}/"}},
    }


def _diff_comment(comment_id, text="some comment", file_path=None, line=None):
    dc = {
        "id": comment_id,
        "text": text,
        "first_line": line,
        "issue_opened": False,
        "issue_status": None,
        "links": {},
    }
    if file_path:
        dc["links"]["filediff"] = {"href": "/api/filediffs/1/"}
    return dc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFetchAllComments:
    def test_empty_reviews(self):
        client = _MockClient()
        fetcher = RBCommentFetcher(client, "bot")
        result = fetcher.fetch_all_comments(100)
        assert result == []

    def test_body_comment_extracted(self):
        client = _MockClient(reviews=[_review(1, "alice", body_top="Great work")])
        fetcher = RBCommentFetcher(client, "bot")
        result = fetcher.fetch_all_comments(100)
        assert len(result) == 1
        assert result[0].text == "Great work"
        assert result[0].is_body_comment is True
        assert result[0].reviewer == "alice"

    def test_bot_reviews_filtered(self):
        client = _MockClient(
            reviews=[
                _review(1, "bot", body_top="I am the bot"),
                _review(2, "alice", body_top="Real comment"),
            ]
        )
        fetcher = RBCommentFetcher(client, "bot")
        result = fetcher.fetch_all_comments(100)
        assert len(result) == 1
        assert result[0].reviewer == "alice"

    def test_diff_comments_included(self):
        client = _MockClient(
            reviews=[_review(1, "alice")],
            diff_comments={1: [_diff_comment(10, text="Fix this")]},
        )
        fetcher = RBCommentFetcher(client, "bot")
        result = fetcher.fetch_all_comments(100)
        assert len(result) == 1
        assert result[0].text == "Fix this"
        assert result[0].comment_id == 10
        assert result[0].is_body_comment is False

    def test_replies_included(self):
        reply = {
            "id": 20,
            "body_top": "I disagree",
            "links": {"user": {"href": "/api/users/bob/"}},
        }
        client = _MockClient(
            reviews=[_review(1, "alice", body_top="Check this")],
            replies={1: [reply]},
        )
        fetcher = RBCommentFetcher(client, "bot")
        result = fetcher.fetch_all_comments(100)
        assert len(result) == 2
        # Original body comment + reply
        reviewers = {c.reviewer for c in result}
        assert reviewers == {"alice", "bob"}

    def test_bot_replies_filtered(self):
        reply = {
            "id": 20,
            "body_top": "Bot reply",
            "links": {"user": {"href": "/api/users/bot/"}},
        }
        client = _MockClient(
            reviews=[_review(1, "alice", body_top="Check")],
            replies={1: [reply]},
        )
        fetcher = RBCommentFetcher(client, "bot")
        result = fetcher.fetch_all_comments(100)
        assert len(result) == 1
        assert result[0].reviewer == "alice"

    def test_empty_body_top_skipped(self):
        client = _MockClient(reviews=[_review(1, "alice", body_top="")])
        fetcher = RBCommentFetcher(client, "bot")
        result = fetcher.fetch_all_comments(100)
        assert len(result) == 0
