"""Tests that sync_queue and _sync_one emit reporter events."""

from datetime import datetime
from pathlib import Path

import pytest

from bb_review.db.queue_db import QueueDatabase
from bb_review.models import PendingReview
from bb_review.queue_sync import sync_queue


class _RecordingReporter:
    def __init__(self):
        self.events: list[tuple] = []

    def checkpoint(self, msg):
        self.events.append(('checkpoint', msg))

    def tick(self, current, total):
        self.events.append(('tick', current, total))

    def item_event(self, msg):
        self.events.append(('item', msg))


class _FakeRBClient:
    """Minimal RB client stand-in for sync_queue tests.

    Returns the configured PendingReviews from get_recent_reviews / get_pending_reviews.
    diffs_equal can be configured to return False (signals a real content change).
    """

    def __init__(self, pending: list[PendingReview], diffs_equal_returns: bool = True):
        self._pending = pending
        self._diffs_equal_returns = diffs_equal_returns

    def get_recent_reviews(self, days, limit, repository=None, from_user=None, reporter=None):
        # Mirror the real method's reporter usage so the reporter sees ticks.
        from bb_review.progress import NullProgressReporter
        reporter = reporter or NullProgressReporter()
        reporter.checkpoint(
            f'Fetching review requests from RB (last {days} days, max {limit})...'
        )
        total = len(self._pending)
        reporter.checkpoint(f'Got {total} review requests from RB, hydrating...')
        for i in range(total):
            reporter.tick(i + 1, total)
        return list(self._pending)

    def get_pending_reviews(self, limit, reporter=None):
        return list(self._pending)

    def diffs_equal(self, rr_id, rev_a, rev_b):
        return self._diffs_equal_returns


def _make_pending(rr_id: int, diff_revision: int = 1) -> PendingReview:
    return PendingReview(
        review_request_id=rr_id,
        repository='test-repo',
        submitter='dev',
        summary=f'rr {rr_id}',
        diff_revision=diff_revision,
        base_commit='abc123',
        branch='main',
        created_at=datetime(2026, 5, 24),
        issue_open_count=0,
        ship_it_count=0,
    )


@pytest.fixture
def queue_db(tmp_path: Path) -> QueueDatabase:
    return QueueDatabase(tmp_path / 'queue.db')


def test_sync_queue_emits_reconcile_checkpoint(queue_db: QueueDatabase):
    pending = [_make_pending(101), _make_pending(102), _make_pending(103)]
    client = _FakeRBClient(pending)
    reporter = _RecordingReporter()

    sync_queue(rb_client=client, queue_db=queue_db, days=10, reporter=reporter)

    kinds = [e[0] for e in reporter.events]
    assert ('checkpoint', 'Reconciling 3 review requests against local queue...') in reporter.events
    # Ticks come from the (faked) get_recent_reviews.
    assert kinds.count('tick') == 3


def test_sync_queue_skips_reconcile_checkpoint_when_empty(queue_db: QueueDatabase):
    client = _FakeRBClient(pending=[])
    reporter = _RecordingReporter()

    sync_queue(rb_client=client, queue_db=queue_db, days=10, reporter=reporter)

    reconcile_msgs = [
        msg for kind, msg in [(e[0], e[1] if len(e) > 1 else None) for e in reporter.events]
        if kind == 'checkpoint' and msg and 'Reconciling' in msg
    ]
    assert reconcile_msgs == []


def test_sync_queue_emits_item_event_when_diff_revision_changes(queue_db: QueueDatabase):
    # First sync: rr 200 enters at diff_revision=1.
    client1 = _FakeRBClient([_make_pending(200, diff_revision=1)])
    sync_queue(rb_client=client1, queue_db=queue_db, days=10)

    # Second sync: same rr now at diff_revision=2 with a real content change.
    client2 = _FakeRBClient(
        [_make_pending(200, diff_revision=2)],
        diffs_equal_returns=False,
    )
    reporter = _RecordingReporter()
    sync_queue(rb_client=client2, queue_db=queue_db, days=10, reporter=reporter, prune=False)

    item_events = [e for e in reporter.events if e[0] == 'item']
    assert item_events == [
        ('item', 'r/200: checking diff 1->2 for content change...'),
    ]


def test_sync_queue_works_without_reporter(queue_db: QueueDatabase):
    client = _FakeRBClient([_make_pending(101)])
    # No reporter passed — must not crash.
    counts = sync_queue(rb_client=client, queue_db=queue_db, days=10)
    assert counts['total'] == 1
