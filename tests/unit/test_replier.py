"""Tests for RBReplier from bb_review/triage/replier.py."""

from bb_review.triage.models import FixPlanItem, TriageAction
from bb_review.triage.replier import RBReplier


# ---------------------------------------------------------------------------
# Minimal mock RB client for reply operations
# ---------------------------------------------------------------------------


class _MockReplyClient:
    """Tracks calls to reply-related RB API methods."""

    def __init__(self):
        self.replies_created: list[dict] = []
        self.diff_comment_replies: list[dict] = []
        self.published: list[dict] = []
        self._next_reply_id = 100

    def post_reply(self, rr_id, review_id, body_top=""):
        reply_id = self._next_reply_id
        self._next_reply_id += 1
        self.replies_created.append(
            {
                "rr_id": rr_id,
                "review_id": review_id,
                "body_top": body_top,
            }
        )
        return {"id": reply_id}

    def post_diff_comment_reply(self, rr_id, review_id, reply_id, reply_to_id, text):
        self.diff_comment_replies.append(
            {
                "rr_id": rr_id,
                "review_id": review_id,
                "reply_id": reply_id,
                "reply_to_id": reply_to_id,
                "text": text,
            }
        )

    def publish_reply(self, rr_id, review_id, reply_id):
        self.published.append(
            {
                "rr_id": rr_id,
                "review_id": review_id,
                "reply_id": reply_id,
            }
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRBReplier:
    def test_no_replyable_items_returns_empty(self):
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [FixPlanItem(comment_id=1, action=TriageAction.SKIP)]
        result = replier.post_replies(100, items, {1: 10})
        assert result == []
        assert len(client.replies_created) == 0

    def test_reply_item_posted(self):
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [
            FixPlanItem(
                comment_id=1,
                action=TriageAction.REPLY,
                reply_text="Thanks for the feedback",
                file_path="src/main.c",
                line_number=42,
            )
        ]
        review_map = {1: 10}

        result = replier.post_replies(100, items, review_map)

        assert len(result) == 1
        assert len(client.replies_created) == 1
        assert len(client.diff_comment_replies) == 1
        assert client.diff_comment_replies[0]["text"] == "Thanks for the feedback"
        assert len(client.published) == 1

    def test_body_reply_uses_body_top(self):
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [
            FixPlanItem(
                comment_id=1,
                action=TriageAction.REPLY,
                reply_text="Body reply text",
                # No file_path = body comment
            )
        ]
        review_map = {1: 10}

        replier.post_replies(100, items, review_map)

        assert len(client.replies_created) == 1
        assert client.replies_created[0]["body_top"] == "Body reply text"
        assert len(client.diff_comment_replies) == 0  # no diff reply for body

    def test_groups_by_review_id(self):
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [
            FixPlanItem(
                comment_id=1, action=TriageAction.REPLY, reply_text="Reply 1", file_path="a.c", line_number=1
            ),
            FixPlanItem(
                comment_id=2, action=TriageAction.REPLY, reply_text="Reply 2", file_path="b.c", line_number=2
            ),
            FixPlanItem(
                comment_id=3, action=TriageAction.REPLY, reply_text="Reply 3", file_path="c.c", line_number=3
            ),
        ]
        # comments 1, 2 belong to review 10; comment 3 to review 20
        review_map = {1: 10, 2: 10, 3: 20}

        result = replier.post_replies(100, items, review_map)

        assert len(result) == 2  # 2 reviews
        assert len(client.replies_created) == 2
        assert len(client.diff_comment_replies) == 3

    def test_dry_run_posts_nothing(self):
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [
            FixPlanItem(
                comment_id=1,
                action=TriageAction.REPLY,
                reply_text="Would be posted",
                file_path="a.c",
                line_number=1,
            )
        ]
        review_map = {1: 10}

        result = replier.post_replies(100, items, review_map, dry_run=True)

        assert result == []
        assert len(client.replies_created) == 0

    def test_fix_items_with_reply_text_posted(self):
        """Fix items that also have reply text should still post replies."""
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [
            FixPlanItem(
                comment_id=1,
                action=TriageAction.FIX,
                reply_text="Will fix in next revision",
                file_path="a.c",
                line_number=10,
            )
        ]
        review_map = {1: 10}

        result = replier.post_replies(100, items, review_map)
        assert len(result) == 1

    def test_skip_items_not_posted(self):
        """Skip items should not post replies even with reply_text."""
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [
            FixPlanItem(
                comment_id=1,
                action=TriageAction.SKIP,
                reply_text="This is ignored",
            )
        ]
        review_map = {1: 10}

        result = replier.post_replies(100, items, review_map)
        assert result == []

    def test_missing_review_id_skipped(self):
        client = _MockReplyClient()
        replier = RBReplier(client)
        items = [
            FixPlanItem(
                comment_id=99,
                action=TriageAction.REPLY,
                reply_text="Some text",
                file_path="a.c",
                line_number=1,
            )
        ]
        # comment 99 not in map
        review_map = {}

        result = replier.post_replies(100, items, review_map)
        assert result == []
