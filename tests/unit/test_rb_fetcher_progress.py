"""Tests that RBCommentFetcher.fetch_all_comments emits reporter events."""

from bb_review.rr.rb_fetcher import RBCommentFetcher


class _RecordingReporter:
    def __init__(self):
        self.events: list[tuple] = []

    def checkpoint(self, msg):
        self.events.append(('checkpoint', msg))

    def tick(self, current, total):
        self.events.append(('tick', current, total))

    def item_event(self, msg):
        self.events.append(('item', msg))


class _StubRBClient:
    """Minimal stub that returns N reviews with no diff comments or replies."""

    def __init__(self, review_ids: list[int]):
        self._review_ids = review_ids
        self._filediff_cache: dict[int, list] = {}

    def get_reviews(self, rr_id):
        return [
            {
                'id': rid,
                'body_top': '',
                'links': {'user': {'href': '/api/users/dev/'}},
            }
            for rid in self._review_ids
        ]

    def _warm_filediff_cache(self, rr_id):
        pass

    def get_review_diff_comments(self, rr_id, review_id):
        return []

    def get_review_replies(self, rr_id, review_id):
        return []


def test_fetch_all_comments_emits_checkpoint_and_ticks():
    client = _StubRBClient(review_ids=[10, 11, 12])
    fetcher = RBCommentFetcher(client, bot_username='bot')
    reporter = _RecordingReporter()

    comments = fetcher.fetch_all_comments(rr_id=42, reporter=reporter)

    assert comments == []  # stub returns no body/diff/reply content
    assert reporter.events == [
        ('checkpoint', 'Fetching comments for r/42...'),
        ('tick', 1, 3),
        ('tick', 2, 3),
        ('tick', 3, 3),
    ]


def test_fetch_all_comments_works_without_reporter():
    client = _StubRBClient(review_ids=[10])
    fetcher = RBCommentFetcher(client, bot_username='bot')
    # No reporter passed — must not crash.
    comments = fetcher.fetch_all_comments(rr_id=42)
    assert comments == []
