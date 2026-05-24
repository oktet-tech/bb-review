"""Tests that get_recent_reviews / get_pending_reviews emit reporter events."""

from bb_review.models import PendingReview
from bb_review.rr.rb_client import ReviewBoardClient


class _RecordingReporter:
    """Captures ProgressReporter events as a list of tuples."""

    def __init__(self):
        self.events: list[tuple] = []

    def checkpoint(self, msg):
        self.events.append(('checkpoint', msg))

    def tick(self, current, total):
        self.events.append(('tick', current, total))

    def item_event(self, msg):
        self.events.append(('item', msg))


def _fake_pending_review(rr_id: int) -> PendingReview:
    return PendingReview(
        review_request_id=rr_id,
        repository='test-repo',
        submitter='dev',
        summary=f'rr {rr_id}',
        diff_revision=1,
        base_commit='abc123',
        branch='main',
        created_at=None,
        issue_open_count=0,
        ship_it_count=0,
    )


def _install_three_rrs(client, monkeypatch):
    """Make client.get_*_reviews see 3 RRs, skipping the real API/hydration."""
    monkeypatch.setattr(
        client,
        '_api_get',
        lambda path, params=None: {
            'review_requests': [{'id': 101}, {'id': 102}, {'id': 103}],
        },
    )
    monkeypatch.setattr(
        client,
        '_to_pending_review',
        lambda rr: _fake_pending_review(rr['id']),
    )


def test_get_recent_reviews_emits_checkpoints_and_ticks(monkeypatch):
    client = ReviewBoardClient(url='https://rb.example.com', bot_username='bot')
    _install_three_rrs(client, monkeypatch)
    reporter = _RecordingReporter()

    result = client.get_recent_reviews(days=10, limit=200, reporter=reporter)

    assert [pr.review_request_id for pr in result] == [101, 102, 103]
    assert reporter.events == [
        ('checkpoint', 'Fetching review requests from RB (last 10 days, max 200)...'),
        ('checkpoint', 'Got 3 review requests from RB, hydrating...'),
        ('tick', 1, 3),
        ('tick', 2, 3),
        ('tick', 3, 3),
    ]


def test_get_recent_reviews_works_without_reporter(monkeypatch):
    client = ReviewBoardClient(url='https://rb.example.com', bot_username='bot')
    _install_three_rrs(client, monkeypatch)
    # No reporter passed — must not crash.
    result = client.get_recent_reviews(days=10, limit=200)
    assert len(result) == 3


def test_get_pending_reviews_emits_checkpoints_and_ticks(monkeypatch):
    client = ReviewBoardClient(url='https://rb.example.com', bot_username='bot')
    monkeypatch.setattr(
        client,
        '_api_get',
        lambda path, params=None: {
            'review_requests': [{'id': 201}, {'id': 202}],
        },
    )
    monkeypatch.setattr(client, '_has_bot_reviewed', lambda _rr_id: False)
    monkeypatch.setattr(
        client,
        '_to_pending_review',
        lambda rr: _fake_pending_review(rr['id']),
    )
    reporter = _RecordingReporter()

    result = client.get_pending_reviews(limit=50, reporter=reporter)

    assert [pr.review_request_id for pr in result] == [201, 202]
    assert reporter.events == [
        ('checkpoint', 'Fetching pending reviews assigned to bot...'),
        ('checkpoint', 'Got 2 review requests from RB, hydrating...'),
        ('tick', 1, 2),
        ('tick', 2, 2),
    ]
