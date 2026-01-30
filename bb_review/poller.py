"""Poller for monitoring pending reviews and state tracking."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
import logging
from pathlib import Path
import sqlite3
import time

from .models import PendingReview, ProcessedReview


logger = logging.getLogger(__name__)


class StateDatabase:
    """SQLite database for tracking processed reviews."""

    def __init__(self, db_path: Path):
        """Initialize the state database.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Ensure database and tables exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_request_id INTEGER NOT NULL,
                    diff_revision INTEGER NOT NULL,
                    processed_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    error_message TEXT,
                    comment_count INTEGER DEFAULT 0,
                    UNIQUE(review_request_id, diff_revision)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_review_request
                ON processed_reviews(review_request_id)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS poll_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_poll_at TEXT,
                    last_poll_count INTEGER DEFAULT 0
                )
            """)

            # Initialize poll state if not exists
            conn.execute("""
                INSERT OR IGNORE INTO poll_state (id, last_poll_at, last_poll_count)
                VALUES (1, NULL, 0)
            """)

            conn.commit()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def is_processed(self, review_request_id: int, diff_revision: int) -> bool:
        """Check if a review has already been processed.

        Args:
            review_request_id: Review request ID.
            diff_revision: Diff revision number.

        Returns:
            True if already processed.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM processed_reviews
                WHERE review_request_id = ? AND diff_revision = ?
                """,
                (review_request_id, diff_revision),
            )
            return cursor.fetchone() is not None

    def mark_processed(
        self,
        review_request_id: int,
        diff_revision: int,
        success: bool,
        error_message: str | None = None,
        comment_count: int = 0,
    ) -> None:
        """Mark a review as processed.

        Args:
            review_request_id: Review request ID.
            diff_revision: Diff revision number.
            success: Whether processing succeeded.
            error_message: Error message if failed.
            comment_count: Number of comments posted.
        """
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_reviews
                (review_request_id, diff_revision, processed_at, success, error_message, comment_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    review_request_id,
                    diff_revision,
                    datetime.now().isoformat(),
                    1 if success else 0,
                    error_message,
                    comment_count,
                ),
            )
            conn.commit()

    def get_processed(self, review_request_id: int) -> list[ProcessedReview]:
        """Get all processed records for a review request.

        Args:
            review_request_id: Review request ID.

        Returns:
            List of processed review records.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM processed_reviews
                WHERE review_request_id = ?
                ORDER BY diff_revision DESC
                """,
                (review_request_id,),
            )

            results = []
            for row in cursor:
                results.append(
                    ProcessedReview(
                        review_request_id=row["review_request_id"],
                        diff_revision=row["diff_revision"],
                        processed_at=datetime.fromisoformat(row["processed_at"]),
                        success=bool(row["success"]),
                        error_message=row["error_message"],
                        comment_count=row["comment_count"],
                    )
                )
            return results

    def update_poll_state(self, count: int) -> None:
        """Update the last poll timestamp and count.

        Args:
            count: Number of reviews found in this poll.
        """
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE poll_state
                SET last_poll_at = ?, last_poll_count = ?
                WHERE id = 1
                """,
                (datetime.now().isoformat(), count),
            )
            conn.commit()

    def get_poll_state(self) -> dict:
        """Get the current poll state.

        Returns:
            Dict with last_poll_at and last_poll_count.
        """
        with self._connection() as conn:
            cursor = conn.execute("SELECT * FROM poll_state WHERE id = 1")
            row = cursor.fetchone()
            if row:
                return {
                    "last_poll_at": row["last_poll_at"],
                    "last_poll_count": row["last_poll_count"],
                }
            return {"last_poll_at": None, "last_poll_count": 0}

    def get_stats(self) -> dict:
        """Get statistics about processed reviews.

        Returns:
            Dict with various statistics.
        """
        with self._connection() as conn:
            total = conn.execute("SELECT COUNT(*) as count FROM processed_reviews").fetchone()["count"]

            success = conn.execute(
                "SELECT COUNT(*) as count FROM processed_reviews WHERE success = 1"
            ).fetchone()["count"]

            failed = conn.execute(
                "SELECT COUNT(*) as count FROM processed_reviews WHERE success = 0"
            ).fetchone()["count"]

            total_comments = (
                conn.execute("SELECT SUM(comment_count) as total FROM processed_reviews").fetchone()["total"]
                or 0
            )

            recent = conn.execute(
                """
                SELECT * FROM processed_reviews
                ORDER BY processed_at DESC
                LIMIT 10
                """
            ).fetchall()

            return {
                "total_processed": total,
                "successful": success,
                "failed": failed,
                "total_comments": total_comments,
                "recent": [dict(r) for r in recent],
            }


class Poller:
    """Polls for pending reviews and coordinates processing."""

    def __init__(
        self,
        state_db: StateDatabase,
        interval_seconds: int = 300,
        max_reviews_per_cycle: int = 10,
    ):
        """Initialize the poller.

        Args:
            state_db: State database instance.
            interval_seconds: Polling interval in seconds.
            max_reviews_per_cycle: Maximum reviews to process per cycle.
        """
        self.state_db = state_db
        self.interval_seconds = interval_seconds
        self.max_reviews_per_cycle = max_reviews_per_cycle
        self._running = False

    def filter_pending(self, pending: list[PendingReview]) -> list[PendingReview]:
        """Filter out already processed reviews.

        Args:
            pending: List of pending reviews.

        Returns:
            List of reviews that haven't been processed yet.
        """
        filtered = []
        for review in pending:
            if not self.state_db.is_processed(review.review_request_id, review.diff_revision):
                filtered.append(review)
        return filtered[: self.max_reviews_per_cycle]

    def run_once(
        self,
        fetch_pending_func,
        process_func,
    ) -> int:
        """Run a single poll cycle.

        Args:
            fetch_pending_func: Function to fetch pending reviews.
            process_func: Function to process a single review.

        Returns:
            Number of reviews processed.
        """
        logger.info("Starting poll cycle")

        # Fetch pending reviews
        try:
            all_pending = fetch_pending_func()
        except Exception as e:
            logger.error(f"Failed to fetch pending reviews: {e}")
            return 0

        # Filter to unprocessed
        pending = self.filter_pending(all_pending)
        logger.info(f"Found {len(pending)} reviews to process")

        self.state_db.update_poll_state(len(pending))

        processed_count = 0
        for review in pending:
            try:
                logger.info(f"Processing review {review.review_request_id} (diff rev {review.diff_revision})")
                result = process_func(review)

                self.state_db.mark_processed(
                    review.review_request_id,
                    review.diff_revision,
                    success=True,
                    comment_count=result.issue_count if result else 0,
                )
                processed_count += 1

            except Exception as e:
                logger.error(f"Failed to process review {review.review_request_id}: {e}")
                self.state_db.mark_processed(
                    review.review_request_id,
                    review.diff_revision,
                    success=False,
                    error_message=str(e),
                )

        logger.info(f"Poll cycle complete. Processed {processed_count} reviews.")
        return processed_count

    def run_daemon(
        self,
        fetch_pending_func,
        process_func,
    ) -> None:
        """Run as a daemon, polling continuously.

        Args:
            fetch_pending_func: Function to fetch pending reviews.
            process_func: Function to process a single review.
        """
        logger.info(
            f"Starting polling daemon (interval={self.interval_seconds}s, "
            f"max_per_cycle={self.max_reviews_per_cycle})"
        )

        self._running = True

        while self._running:
            try:
                self.run_once(fetch_pending_func, process_func)
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")

            if self._running:
                logger.debug(f"Sleeping for {self.interval_seconds}s")
                time.sleep(self.interval_seconds)

    def stop(self) -> None:
        """Stop the daemon."""
        logger.info("Stopping polling daemon")
        self._running = False
