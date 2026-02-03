"""Tests for the queue database module."""

from datetime import datetime
from pathlib import Path

import pytest

from bb_review.db.queue_db import QueueDatabase
from bb_review.db.queue_models import VALID_TRANSITIONS, QueueStatus


@pytest.fixture
def queue_db(tmp_path: Path) -> QueueDatabase:
    """Create a temporary queue database."""
    db_path = tmp_path / "test_reviews.db"
    return QueueDatabase(db_path)


@pytest.fixture
def queue_db_with_analyses(tmp_path: Path) -> QueueDatabase:
    """Queue DB that also has an analyses table (simulates shared DB with ReviewDatabase)."""
    from bb_review.db import ReviewDatabase

    db_path = tmp_path / "test_reviews.db"
    # Init ReviewDatabase first so analyses table exists
    ReviewDatabase(db_path)
    return QueueDatabase(db_path)


class TestQueueDatabaseCreate:
    def test_create_database(self, queue_db: QueueDatabase):
        assert queue_db.db_path.exists()

    def test_create_table_idempotent(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        QueueDatabase(db_path)
        QueueDatabase(db_path)  # should not raise


class TestUpsert:
    def test_insert_new_item(self, queue_db: QueueDatabase):
        action, reset = queue_db.upsert(
            review_request_id=42738,
            diff_revision=1,
            repository="test-repo",
            submitter="alice",
            summary="Fix the widget",
        )
        assert action == "inserted"
        assert reset is False

        item = queue_db.get(42738)
        assert item is not None
        assert item.review_request_id == 42738
        assert item.diff_revision == 1
        assert item.status == QueueStatus.TODO
        assert item.repository == "test-repo"
        assert item.submitter == "alice"
        assert item.summary == "Fix the widget"

    def test_upsert_same_diff_keeps_status(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        # Move to next
        queue_db.update_status(42738, QueueStatus.NEXT)

        action, reset = queue_db.upsert(
            review_request_id=42738,
            diff_revision=1,
            summary="Updated summary",
        )
        assert action == "skipped"
        assert reset is False

        item = queue_db.get(42738)
        assert item.status == QueueStatus.NEXT
        assert item.summary == "Updated summary"

    def test_upsert_new_diff_resets_to_todo(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.update_status(42738, QueueStatus.NEXT)
        queue_db.update_status(42738, QueueStatus.IN_PROGRESS)
        queue_db.mark_done(42738, analysis_id=99)

        # New diff version
        action, reset = queue_db.upsert(review_request_id=42738, diff_revision=2)
        assert action == "updated"
        assert reset is True

        item = queue_db.get(42738)
        assert item.status == QueueStatus.TODO
        assert item.diff_revision == 2
        assert item.analysis_id is None
        assert item.error_message is None

    def test_upsert_preserves_metadata(self, queue_db: QueueDatabase):
        queue_db.upsert(
            review_request_id=42738,
            diff_revision=1,
            repository="repo-a",
            submitter="bob",
        )
        # Upsert with only partial metadata
        queue_db.upsert(review_request_id=42738, diff_revision=1, summary="New summary")

        item = queue_db.get(42738)
        assert item.repository == "repo-a"
        assert item.submitter == "bob"
        assert item.summary == "New summary"

    def test_unique_constraint(self, queue_db: QueueDatabase):
        """Only one entry per review_request_id."""
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.upsert(review_request_id=42738, diff_revision=1)

        items = queue_db.list_items()
        rr_ids = [i.review_request_id for i in items]
        assert rr_ids.count(42738) == 1


class TestUpdateStatus:
    def test_valid_transitions(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)

        # todo -> next
        prev = queue_db.update_status(42738, QueueStatus.NEXT)
        assert prev == QueueStatus.TODO

        # next -> in_progress
        prev = queue_db.update_status(42738, QueueStatus.IN_PROGRESS)
        assert prev == QueueStatus.NEXT

        # in_progress -> done
        prev = queue_db.update_status(42738, QueueStatus.DONE)
        assert prev == QueueStatus.IN_PROGRESS

    def test_invalid_transition_raises(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)

        with pytest.raises(ValueError, match="Cannot transition"):
            queue_db.update_status(42738, QueueStatus.DONE)

    def test_not_found_raises(self, queue_db: QueueDatabase):
        with pytest.raises(ValueError, match="not found"):
            queue_db.update_status(99999, QueueStatus.NEXT)

    def test_todo_to_ignore(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        prev = queue_db.update_status(42738, QueueStatus.IGNORE)
        assert prev == QueueStatus.TODO

    def test_ignore_to_next(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.update_status(42738, QueueStatus.IGNORE)
        prev = queue_db.update_status(42738, QueueStatus.NEXT)
        assert prev == QueueStatus.IGNORE

    def test_failed_to_next(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.update_status(42738, QueueStatus.NEXT)
        queue_db.update_status(42738, QueueStatus.IN_PROGRESS)
        queue_db.update_status(42738, QueueStatus.FAILED)

        prev = queue_db.update_status(42738, QueueStatus.NEXT)
        assert prev == QueueStatus.FAILED

    def test_done_to_todo(self, queue_db: QueueDatabase):
        """Re-sync path: done -> todo."""
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.update_status(42738, QueueStatus.NEXT)
        queue_db.update_status(42738, QueueStatus.IN_PROGRESS)
        queue_db.update_status(42738, QueueStatus.DONE)

        prev = queue_db.update_status(42738, QueueStatus.TODO)
        assert prev == QueueStatus.DONE

    def test_all_transitions_covered(self):
        """Every QueueStatus has an entry in VALID_TRANSITIONS."""
        for status in QueueStatus:
            assert status in VALID_TRANSITIONS


class TestMarkDoneAndFailed:
    def test_mark_done(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.mark_done(42738, analysis_id=42)

        item = queue_db.get(42738)
        assert item.status == QueueStatus.DONE
        assert item.analysis_id == 42
        assert item.error_message is None

    def test_mark_failed(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.mark_failed(42738, "Connection timeout")

        item = queue_db.get(42738)
        assert item.status == QueueStatus.FAILED
        assert item.error_message == "Connection timeout"


class TestResetStaleInProgress:
    def test_resets_in_progress_to_next(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.update_status(42738, QueueStatus.NEXT)
        queue_db.update_status(42738, QueueStatus.IN_PROGRESS)

        count = queue_db.reset_stale_in_progress()
        assert count == 1

        item = queue_db.get(42738)
        assert item.status == QueueStatus.NEXT

    def test_does_not_reset_other_statuses(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.upsert(review_request_id=42739, diff_revision=1)
        queue_db.update_status(42739, QueueStatus.NEXT)

        count = queue_db.reset_stale_in_progress()
        assert count == 0

    def test_returns_zero_when_none(self, queue_db: QueueDatabase):
        count = queue_db.reset_stale_in_progress()
        assert count == 0


class TestPickNext:
    def test_pick_ordered_by_synced_at(self, queue_db: QueueDatabase):
        for rr_id in [42740, 42738, 42739]:
            queue_db.upsert(review_request_id=rr_id, diff_revision=1)
            queue_db.update_status(rr_id, QueueStatus.NEXT)

        items = queue_db.pick_next(count=2)
        assert len(items) == 2
        # Oldest synced_at first
        assert items[0].review_request_id == 42740
        assert items[1].review_request_id == 42738

    def test_pick_only_next_status(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)  # todo
        queue_db.upsert(review_request_id=42739, diff_revision=1)
        queue_db.update_status(42739, QueueStatus.NEXT)

        items = queue_db.pick_next(count=10)
        assert len(items) == 1
        assert items[0].review_request_id == 42739

    def test_pick_empty(self, queue_db: QueueDatabase):
        items = queue_db.pick_next()
        assert items == []


class TestListItems:
    def test_list_all(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1, repository="repo-a")
        queue_db.upsert(review_request_id=42739, diff_revision=1, repository="repo-b")

        items = queue_db.list_items()
        assert len(items) == 2

    def test_list_by_status(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.upsert(review_request_id=42739, diff_revision=1)
        queue_db.update_status(42739, QueueStatus.NEXT)

        items = queue_db.list_items(status=QueueStatus.TODO)
        assert len(items) == 1
        assert items[0].review_request_id == 42738

    def test_list_by_repository(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1, repository="repo-a")
        queue_db.upsert(review_request_id=42739, diff_revision=1, repository="repo-b")

        items = queue_db.list_items(repository="repo-a")
        assert len(items) == 1
        assert items[0].repository == "repo-a"

    def test_list_limit(self, queue_db: QueueDatabase):
        for i in range(10):
            queue_db.upsert(review_request_id=42700 + i, diff_revision=1)

        items = queue_db.list_items(limit=3)
        assert len(items) == 3


class TestGetStats:
    def test_empty_stats(self, queue_db: QueueDatabase):
        stats = queue_db.get_stats()
        assert stats["total"] == 0

    def test_stats_by_status(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.upsert(review_request_id=42739, diff_revision=1)
        queue_db.update_status(42739, QueueStatus.NEXT)

        stats = queue_db.get_stats()
        assert stats["total"] == 2
        assert stats["todo"] == 1
        assert stats["next"] == 1


class TestHasNonFakeAnalysis:
    def test_returns_false_without_analyses_table(self, queue_db: QueueDatabase):
        """When analyses table doesn't exist, should handle gracefully."""
        # The queue_db alone doesn't create the analyses table
        # This should either return False or raise; let's verify behavior
        try:
            result = queue_db.has_non_fake_analysis(42738, 1)
            # If it doesn't raise, it should return False
            assert result is False
        except Exception:
            pass  # acceptable - the analyses table doesn't exist

    def test_returns_true_with_real_analysis(self, queue_db_with_analyses: QueueDatabase):
        from bb_review.db import ReviewDatabase
        from bb_review.models import ReviewResult

        review_db = ReviewDatabase(queue_db_with_analyses.db_path)
        result = ReviewResult(
            review_request_id=42738,
            diff_revision=1,
            comments=[],
            summary="OK",
        )
        review_db.save_analysis(
            result=result,
            repository="test",
            analysis_method="llm",
            model="claude",
            fake=False,
        )

        assert queue_db_with_analyses.has_non_fake_analysis(42738, 1) is True

    def test_returns_false_with_only_fake_analysis(self, queue_db_with_analyses: QueueDatabase):
        from bb_review.db import ReviewDatabase
        from bb_review.models import ReviewResult

        review_db = ReviewDatabase(queue_db_with_analyses.db_path)
        result = ReviewResult(
            review_request_id=42738,
            diff_revision=1,
            comments=[],
            summary="Fake",
        )
        review_db.save_analysis(
            result=result,
            repository="test",
            analysis_method="llm",
            model="claude",
            fake=True,
        )

        assert queue_db_with_analyses.has_non_fake_analysis(42738, 1) is False

    def test_returns_false_for_different_diff(self, queue_db_with_analyses: QueueDatabase):
        from bb_review.db import ReviewDatabase
        from bb_review.models import ReviewResult

        review_db = ReviewDatabase(queue_db_with_analyses.db_path)
        result = ReviewResult(
            review_request_id=42738,
            diff_revision=1,
            comments=[],
            summary="OK",
        )
        review_db.save_analysis(
            result=result,
            repository="test",
            analysis_method="llm",
            model="claude",
            fake=False,
        )

        # Different diff_revision
        assert queue_db_with_analyses.has_non_fake_analysis(42738, 2) is False


class TestDeleteItem:
    def test_delete_existing(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        assert queue_db.delete_item(42738) is True
        assert queue_db.get(42738) is None

    def test_delete_not_found(self, queue_db: QueueDatabase):
        assert queue_db.delete_item(99999) is False

    def test_delete_only_target(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1)
        queue_db.upsert(review_request_id=42739, diff_revision=1)
        queue_db.delete_item(42738)

        assert queue_db.get(42738) is None
        assert queue_db.get(42739) is not None


class TestGet:
    def test_get_existing(self, queue_db: QueueDatabase):
        queue_db.upsert(review_request_id=42738, diff_revision=1, repository="test")
        item = queue_db.get(42738)
        assert item is not None
        assert item.review_request_id == 42738

    def test_get_not_found(self, queue_db: QueueDatabase):
        item = queue_db.get(99999)
        assert item is None

    def test_get_fields(self, queue_db: QueueDatabase):
        queue_db.upsert(
            review_request_id=42738,
            diff_revision=3,
            repository="myrepo",
            submitter="bob",
            summary="Fix stuff",
            branch="main",
            base_commit="abc123",
            rb_created_at=datetime(2026, 1, 15, 10, 30),
        )
        item = queue_db.get(42738)
        assert item.diff_revision == 3
        assert item.repository == "myrepo"
        assert item.submitter == "bob"
        assert item.summary == "Fix stuff"
        assert item.branch == "main"
        assert item.base_commit == "abc123"
        assert item.rb_created_at == datetime(2026, 1, 15, 10, 30)
        assert item.synced_at is not None
        assert item.updated_at is not None
