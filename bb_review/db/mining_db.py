"""Cache database for mined Review Board reviewer comments.

Lives in its own file (rules_mining.db) so it can be deleted and
re-fetched without touching reviews.db.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from ..triage.models import RBComment


@dataclass
class MinedReviewRequest:
    """A review request whose comments have been cached."""

    rr_id: int
    repository: str
    rr_status: str
    rr_summary: str
    submitter: str
    branch: str
    rb_last_updated: str
    fetched_at: str


@dataclass
class MinedComment:
    """A single cached reviewer comment, joined with its RR status."""

    rr_id: int
    rr_status: str
    review_id: int
    comment_id: int
    reviewer: str
    text: str
    file_path: str | None
    line_number: int | None
    is_body_comment: bool
    issue_opened: bool
    issue_status: str | None
    reply_to_id: int | None


@dataclass
class RepoMiningStats:
    """Summary of what is cached for a repository."""

    repository: str
    review_request_count: int
    comment_count: int


class MiningDatabase:
    """SQLite cache of human reviewer comments fetched from Review Board."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create the cache tables if they do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mined_review_requests (
                    rr_id INTEGER PRIMARY KEY,
                    repository TEXT NOT NULL,
                    rr_status TEXT NOT NULL,
                    rr_summary TEXT,
                    submitter TEXT,
                    branch TEXT,
                    rb_last_updated TEXT,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mined_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rr_id INTEGER NOT NULL
                        REFERENCES mined_review_requests(rr_id) ON DELETE CASCADE,
                    review_id INTEGER NOT NULL,
                    comment_id INTEGER NOT NULL,
                    reviewer TEXT,
                    text TEXT NOT NULL,
                    file_path TEXT,
                    line_number INTEGER,
                    is_body_comment INTEGER NOT NULL DEFAULT 0,
                    issue_opened INTEGER NOT NULL DEFAULT 0,
                    issue_status TEXT,
                    reply_to_id INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_mined_rr_repository
                    ON mined_review_requests(repository);
                CREATE INDEX IF NOT EXISTS idx_mined_comments_rr_id
                    ON mined_comments(rr_id);
                """
            )

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

    def has_review_request(self, rr_id: int) -> bool:
        """Return True if this RR has already been cached."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM mined_review_requests WHERE rr_id = ?",
                (rr_id,),
            ).fetchone()
            return row is not None

    def record_review_request(
        self,
        rr_id: int,
        repository: str,
        rr_status: str,
        rr_summary: str,
        submitter: str,
        branch: str,
        rb_last_updated: str,
        comments: list[RBComment],
    ) -> None:
        """Insert or replace a review request and all its comments.

        Any existing rows for this RR are deleted first, so a --refresh
        re-fetch is idempotent (the comment cascade clears old comments).
        """
        now = datetime.now().isoformat()
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM mined_review_requests WHERE rr_id = ?",
                (rr_id,),
            )
            conn.execute(
                """
                INSERT INTO mined_review_requests
                    (rr_id, repository, rr_status, rr_summary, submitter,
                     branch, rb_last_updated, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rr_id,
                    repository,
                    rr_status,
                    rr_summary,
                    submitter,
                    branch,
                    rb_last_updated,
                    now,
                ),
            )
            for c in comments:
                conn.execute(
                    """
                    INSERT INTO mined_comments
                        (rr_id, review_id, comment_id, reviewer, text,
                         file_path, line_number, is_body_comment,
                         issue_opened, issue_status, reply_to_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rr_id,
                        c.review_id,
                        c.comment_id,
                        c.reviewer,
                        c.text,
                        c.file_path,
                        c.line_number,
                        int(c.is_body_comment),
                        int(c.issue_opened),
                        c.issue_status,
                        c.reply_to_id,
                    ),
                )

    def get_comments_for_repo(self, repository: str) -> list[MinedComment]:
        """Return all cached comments for a repository, ordered by RR."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT c.rr_id, r.rr_status, c.review_id, c.comment_id,
                       c.reviewer, c.text, c.file_path, c.line_number,
                       c.is_body_comment, c.issue_opened, c.issue_status,
                       c.reply_to_id
                FROM mined_comments c
                JOIN mined_review_requests r ON r.rr_id = c.rr_id
                WHERE r.repository = ?
                ORDER BY c.rr_id, c.id
                """,
                (repository,),
            ).fetchall()
        return [
            MinedComment(
                rr_id=row["rr_id"],
                rr_status=row["rr_status"],
                review_id=row["review_id"],
                comment_id=row["comment_id"],
                reviewer=row["reviewer"],
                text=row["text"],
                file_path=row["file_path"],
                line_number=row["line_number"],
                is_body_comment=bool(row["is_body_comment"]),
                issue_opened=bool(row["issue_opened"]),
                issue_status=row["issue_status"],
                reply_to_id=row["reply_to_id"],
            )
            for row in rows
        ]

    def get_repo_stats(self, repository: str) -> RepoMiningStats:
        """Return cached RR and comment counts for a repository."""
        with self._connection() as conn:
            rr_count = conn.execute(
                "SELECT COUNT(*) FROM mined_review_requests WHERE repository = ?",
                (repository,),
            ).fetchone()[0]
            comment_count = conn.execute(
                """
                SELECT COUNT(*) FROM mined_comments c
                JOIN mined_review_requests r ON r.rr_id = c.rr_id
                WHERE r.repository = ?
                """,
                (repository,),
            ).fetchone()[0]
        return RepoMiningStats(
            repository=repository,
            review_request_count=rr_count,
            comment_count=comment_count,
        )
