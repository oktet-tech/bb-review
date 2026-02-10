"""Queue database for managing the review triage workflow."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import logging
from pathlib import Path
import sqlite3

from .queue_models import VALID_TRANSITIONS, QueueItem, QueueStatus


logger = logging.getLogger(__name__)


class QueueDatabase:
    """Database for managing the review queue.

    Shares the same DB file as ReviewDatabase. Each class manages
    its own tables via CREATE TABLE IF NOT EXISTS.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create the review_queue table if it doesn't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_request_id INTEGER NOT NULL,
                    diff_revision INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'todo',
                    repository TEXT,
                    submitter TEXT,
                    summary TEXT,
                    branch TEXT,
                    base_commit TEXT,
                    rb_created_at TEXT,
                    synced_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    analysis_id INTEGER,
                    error_message TEXT,
                    UNIQUE(review_request_id)
                );

                CREATE INDEX IF NOT EXISTS idx_queue_status
                    ON review_queue(status);
                CREATE INDEX IF NOT EXISTS idx_queue_rr_id
                    ON review_queue(review_request_id);
                """
            )
            # Migration: add issue_open_count if missing
            cols = {row[1] for row in conn.execute("PRAGMA table_info(review_queue)").fetchall()}
            if "issue_open_count" not in cols:
                conn.execute("ALTER TABLE review_queue ADD COLUMN issue_open_count INTEGER DEFAULT 0")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(
        self,
        review_request_id: int,
        diff_revision: int,
        repository: str | None = None,
        submitter: str | None = None,
        summary: str | None = None,
        branch: str | None = None,
        base_commit: str | None = None,
        rb_created_at: datetime | None = None,
        issue_open_count: int = 0,
    ) -> tuple[str, bool]:
        """Insert or update a queue item during sync.

        Returns:
            (action, reset) where action is 'inserted' or 'updated' or 'skipped',
            and reset indicates if status was reset to todo.
        """
        now = datetime.now().isoformat()
        rb_created_str = rb_created_at.isoformat() if rb_created_at else None

        with self._connection() as conn:
            existing = conn.execute(
                "SELECT * FROM review_queue WHERE review_request_id = ?",
                (review_request_id,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO review_queue (
                        review_request_id, diff_revision, status, repository,
                        submitter, summary, branch, base_commit, rb_created_at,
                        issue_open_count, synced_at, updated_at
                    ) VALUES (?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_request_id,
                        diff_revision,
                        repository,
                        submitter,
                        summary,
                        branch,
                        base_commit,
                        rb_created_str,
                        issue_open_count,
                        now,
                        now,
                    ),
                )
                return ("inserted", False)

            if existing["diff_revision"] != diff_revision:
                # New diff version: reset to todo, clear analysis link
                conn.execute(
                    """
                    UPDATE review_queue
                    SET diff_revision = ?, status = 'todo', analysis_id = NULL,
                        error_message = NULL, repository = COALESCE(?, repository),
                        submitter = COALESCE(?, submitter),
                        summary = COALESCE(?, summary),
                        branch = COALESCE(?, branch),
                        base_commit = COALESCE(?, base_commit),
                        rb_created_at = COALESCE(?, rb_created_at),
                        issue_open_count = ?,
                        synced_at = ?, updated_at = ?
                    WHERE review_request_id = ?
                    """,
                    (
                        diff_revision,
                        repository,
                        submitter,
                        summary,
                        branch,
                        base_commit,
                        rb_created_str,
                        issue_open_count,
                        now,
                        now,
                        review_request_id,
                    ),
                )
                return ("updated", True)

            # Same diff_revision: update metadata, keep status
            conn.execute(
                """
                UPDATE review_queue
                SET repository = COALESCE(?, repository),
                    submitter = COALESCE(?, submitter),
                    summary = COALESCE(?, summary),
                    branch = COALESCE(?, branch),
                    base_commit = COALESCE(?, base_commit),
                    rb_created_at = COALESCE(?, rb_created_at),
                    issue_open_count = ?,
                    synced_at = ?
                WHERE review_request_id = ?
                """,
                (
                    repository,
                    submitter,
                    summary,
                    branch,
                    base_commit,
                    rb_created_str,
                    issue_open_count,
                    now,
                    review_request_id,
                ),
            )
            return ("skipped", False)

    def update_status(
        self,
        review_request_id: int,
        new_status: QueueStatus,
    ) -> QueueStatus:
        """Transition a queue item to a new status.

        Validates against VALID_TRANSITIONS.

        Returns:
            Previous status.

        Raises:
            ValueError: If transition is invalid or item not found.
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT status FROM review_queue WHERE review_request_id = ?",
                (review_request_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"Queue item not found: r/{review_request_id}")

            current = QueueStatus(row["status"])
            allowed = VALID_TRANSITIONS.get(current, set())

            if new_status not in allowed:
                allowed_str = ", ".join(s.value for s in allowed)
                raise ValueError(
                    f"Cannot transition r/{review_request_id} from {current.value} "
                    f"to {new_status.value}. Allowed: {allowed_str}"
                )

            conn.execute(
                "UPDATE review_queue SET status = ?, updated_at = ? WHERE review_request_id = ?",
                (new_status.value, datetime.now().isoformat(), review_request_id),
            )
            return current

    def mark_done(self, review_request_id: int, analysis_id: int) -> None:
        """Mark a queue item as done with its analysis_id."""
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'done', analysis_id = ?, error_message = NULL, updated_at = ?
                WHERE review_request_id = ?
                """,
                (analysis_id, datetime.now().isoformat(), review_request_id),
            )

    def mark_failed(self, review_request_id: int, error_message: str) -> None:
        """Mark a queue item as failed with an error message."""
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE review_queue
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE review_request_id = ?
                """,
                (error_message, datetime.now().isoformat(), review_request_id),
            )

    def mark_in_progress(self, review_request_id: int) -> None:
        """Mark a queue item as in_progress."""
        self.update_status(review_request_id, QueueStatus.IN_PROGRESS)

    def reset_stale_in_progress(self) -> int:
        """Reset in_progress items back to next (crash recovery).

        Returns:
            Number of items reset.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE review_queue
                SET status = 'next', updated_at = ?
                WHERE status = 'in_progress'
                """,
                (datetime.now().isoformat(),),
            )
            return cursor.rowcount

    def pick_next(self, count: int = 1) -> list[QueueItem]:
        """Pick items with status=next, ordered by synced_at ASC."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM review_queue
                WHERE status = 'next'
                ORDER BY synced_at ASC
                LIMIT ?
                """,
                (count,),
            ).fetchall()
            return [self._row_to_item(r) for r in rows]

    def get(self, review_request_id: int) -> QueueItem | None:
        """Get a single queue item by review request ID."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM review_queue WHERE review_request_id = ?",
                (review_request_id,),
            ).fetchone()
            return self._row_to_item(row) if row else None

    def list_items(
        self,
        status: QueueStatus | None = None,
        repository: str | None = None,
        limit: int = 50,
        exclude_statuses: list[QueueStatus] | None = None,
    ) -> list[QueueItem]:
        """List queue items with optional filters."""
        conditions = []
        params: list = []

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if exclude_statuses:
            placeholders = ", ".join("?" for _ in exclude_statuses)
            conditions.append(f"status NOT IN ({placeholders})")
            params.extend(s.value for s in exclude_statuses)
        if repository is not None:
            conditions.append("repository = ?")
            params.append(repository)

        where = " AND ".join(conditions) if conditions else "1=1"

        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM review_queue
                WHERE {where}
                ORDER BY synced_at DESC
                LIMIT ?
                """,
                params + [limit],
            ).fetchall()
            return [self._row_to_item(r) for r in rows]

    def delete_item(self, review_request_id: int) -> bool:
        """Delete a queue item by review request ID.

        Returns:
            True if the item was deleted, False if not found.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM review_queue WHERE review_request_id = ?",
                (review_request_id,),
            )
            return cursor.rowcount > 0

    def get_stats(self) -> dict[str, int]:
        """Get count of items by status."""
        with self._connection() as conn:
            rows = conn.execute("SELECT status, COUNT(*) as cnt FROM review_queue GROUP BY status").fetchall()
            stats = {row["status"]: row["cnt"] for row in rows}
            stats["total"] = sum(stats.values())
            return stats

    def has_non_fake_analysis(
        self,
        review_request_id: int,
        diff_revision: int,
    ) -> bool:
        """Check if a non-fake analysis exists in the analyses table."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM analyses
                WHERE review_request_id = ? AND diff_revision = ? AND fake = 0
                LIMIT 1
                """,
                (review_request_id, diff_revision),
            ).fetchone()
            return row is not None

    def _row_to_item(self, row: sqlite3.Row) -> QueueItem:
        """Convert a database row to QueueItem."""
        return QueueItem(
            id=row["id"],
            review_request_id=row["review_request_id"],
            diff_revision=row["diff_revision"],
            status=QueueStatus(row["status"]),
            repository=row["repository"],
            submitter=row["submitter"],
            summary=row["summary"],
            branch=row["branch"],
            base_commit=row["base_commit"],
            rb_created_at=(datetime.fromisoformat(row["rb_created_at"]) if row["rb_created_at"] else None),
            synced_at=(datetime.fromisoformat(row["synced_at"]) if row["synced_at"] else None),
            updated_at=(datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None),
            issue_open_count=row["issue_open_count"] or 0,
            analysis_id=row["analysis_id"],
            error_message=row["error_message"],
        )
