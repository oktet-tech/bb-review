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
    diff_revision: int | None = None
    diff_hunk: str | None = None


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
                    reply_to_id INTEGER,
                    diff_revision INTEGER,
                    diff_hunk TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_mined_rr_repository
                    ON mined_review_requests(repository);
                CREATE INDEX IF NOT EXISTS idx_mined_comments_rr_id
                    ON mined_comments(rr_id);
                """
            )
            # Migrations for caches created before the diff columns existed.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(mined_comments)").fetchall()}
            if "diff_revision" not in cols:
                conn.execute("ALTER TABLE mined_comments ADD COLUMN diff_revision INTEGER")
            if "diff_hunk" not in cols:
                conn.execute("ALTER TABLE mined_comments ADD COLUMN diff_hunk TEXT")

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
                         issue_opened, issue_status, reply_to_id,
                         diff_revision, diff_hunk)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        c.diff_revision,
                        c.diff_hunk,
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
                       c.reply_to_id, c.diff_revision, c.diff_hunk
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
                diff_revision=row["diff_revision"],
                diff_hunk=row["diff_hunk"],
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

    def get_comments_missing_hunks(self, rr_id: int) -> list[MinedComment]:
        """Return diff comments for `rr_id` whose `diff_hunk` is NULL.

        Body comments (no `file_path`) are excluded -- they have no hunk to fill.
        Each returned MinedComment carries its `diff_revision` so the backfill
        path can fetch the right diff without re-querying RB.
        """
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT c.rr_id, r.rr_status, c.review_id, c.comment_id,
                       c.reviewer, c.text, c.file_path, c.line_number,
                       c.is_body_comment, c.issue_opened, c.issue_status,
                       c.reply_to_id, c.diff_revision, c.diff_hunk
                FROM mined_comments c
                JOIN mined_review_requests r ON r.rr_id = c.rr_id
                WHERE c.rr_id = ?
                  AND c.diff_hunk IS NULL
                  AND c.file_path IS NOT NULL
                ORDER BY c.id
                """,
                (rr_id,),
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
                diff_revision=row["diff_revision"],
                diff_hunk=row["diff_hunk"],
            )
            for row in rows
        ]

    def update_comment_diff_hunk(self, rr_id: int, comment_id: int, hunk: str) -> None:
        """Set the diff_hunk for a previously-cached diff comment.

        The `file_path IS NOT NULL` guard prevents accidentally writing a
        hunk to a body comment if the caller passes the wrong comment_id.
        """
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE mined_comments
                SET diff_hunk = ?
                WHERE rr_id = ? AND comment_id = ? AND file_path IS NOT NULL
                """,
                (hunk, rr_id, comment_id),
            )
