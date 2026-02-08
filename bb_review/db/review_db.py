"""Reviews database for storing analysis history."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from ..models import ChainReviewResult, ReviewResult
from ..rr.rb_client import DiffInfo, ReviewRequestInfo
from .models import (
    AnalysisListItem,
    AnalysisMethod,
    AnalysisStatus,
    DBStats,
    StoredAnalysis,
    StoredChain,
    StoredComment,
)


class ReviewDatabase:
    """Database for storing review analyses.

    Stores complete analysis history including:
    - Review results with all comments
    - Chain information for dependent reviews
    - Review Board metadata (diff revision, base commit, etc.)
    - Analysis metadata (model used, method, timestamps)
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create database and tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(
                """
                -- Chains table for tracking chain analyses
                CREATE TABLE IF NOT EXISTS chains (
                    chain_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    partial INTEGER NOT NULL DEFAULT 0,
                    failed_at_rr_id INTEGER,
                    branch_name TEXT
                );

                -- Main analyses table
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_request_id INTEGER NOT NULL,
                    diff_revision INTEGER NOT NULL,
                    base_commit_id TEXT,
                    target_commit_id TEXT,
                    repository TEXT NOT NULL,
                    submitter TEXT,
                    rr_summary TEXT,
                    branch TEXT,
                    depends_on_json TEXT,
                    analysis_method TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    analyzed_at TEXT NOT NULL,
                    chain_id TEXT REFERENCES chains(chain_id),
                    chain_position INTEGER,
                    summary TEXT NOT NULL,
                    has_critical_issues INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    submitted_at TEXT,
                    raw_response_path TEXT,
                    fake INTEGER NOT NULL DEFAULT 0,
                    rb_url TEXT,
                    body_top TEXT
                );

                -- Comments table
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
                    file_path TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    issue_type TEXT NOT NULL,
                    suggestion TEXT
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_analyses_rr_id ON analyses(review_request_id);
                CREATE INDEX IF NOT EXISTS idx_analyses_chain_id ON analyses(chain_id);
                CREATE INDEX IF NOT EXISTS idx_analyses_status ON analyses(status);
                CREATE INDEX IF NOT EXISTS idx_analyses_repository ON analyses(repository);
                CREATE INDEX IF NOT EXISTS idx_comments_analysis_id ON comments(analysis_id);
                """
            )
            # Migration: add fake column if it doesn't exist (for existing databases)
            try:
                conn.execute("ALTER TABLE analyses ADD COLUMN fake INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: add rb_url column if it doesn't exist
            try:
                conn.execute("ALTER TABLE analyses ADD COLUMN rb_url TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: add body_top column if it doesn't exist
            try:
                conn.execute("ALTER TABLE analyses ADD COLUMN body_top TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: add diff_context column to comments
            try:
                conn.execute("ALTER TABLE comments ADD COLUMN diff_context TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

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

    def save_analysis(
        self,
        result: ReviewResult,
        repository: str,
        analysis_method: str,
        model: str,
        diff_info: DiffInfo | None = None,
        rr_info: ReviewRequestInfo | None = None,
        chain_id: str | None = None,
        chain_position: int | None = None,
        raw_response_path: str | None = None,
        fake: bool = False,
        rb_url: str | None = None,
        body_top: str | None = None,
        rr_summary: str | None = None,
    ) -> int:
        """Save an analysis result to the database.

        Args:
            result: The review result to save
            repository: Repository name
            analysis_method: "llm" or "opencode"
            model: Model identifier used for analysis
            diff_info: Optional diff information from RB
            rr_info: Optional review request info from RB
            chain_id: Optional chain ID if part of a chain
            chain_position: Position in chain (1-indexed)
            raw_response_path: Optional path to raw LLM response file
            fake: Whether this is a fake/test review
            rb_url: Review Board base URL
            body_top: Full review body text for submission
            rr_summary: Review request summary (overrides rr_info.summary if set)

        Returns:
            The database ID of the saved analysis
        """
        with self._connection() as conn:
            # Prepare data
            base_commit_id = diff_info.base_commit_id if diff_info else None
            target_commit_id = diff_info.target_commit_id if diff_info else None
            submitter = None
            rr_summary_val = rr_summary  # Use direct param if provided
            branch = None
            depends_on_json = None

            if rr_info:
                submitter = getattr(rr_info, "submitter", None)
                if not rr_summary_val:  # Only use rr_info.summary if not directly provided
                    rr_summary_val = rr_info.summary
                branch = getattr(rr_info, "branch", None)
                if rr_info.depends_on:
                    depends_on_json = json.dumps(rr_info.depends_on)

            # Insert analysis
            cursor = conn.execute(
                """
                INSERT INTO analyses (
                    review_request_id, diff_revision, base_commit_id, target_commit_id,
                    repository, submitter, rr_summary, branch, depends_on_json,
                    analysis_method, model_used, analyzed_at, chain_id, chain_position,
                    summary, has_critical_issues, status, raw_response_path, fake, rb_url,
                    body_top
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.review_request_id,
                    result.diff_revision,
                    base_commit_id,
                    target_commit_id,
                    repository,
                    submitter,
                    rr_summary_val,
                    branch,
                    depends_on_json,
                    analysis_method,
                    model,
                    result.analyzed_at.isoformat(),
                    chain_id,
                    chain_position,
                    result.summary,
                    1 if result.has_critical_issues else 0,
                    AnalysisStatus.DRAFT.value,
                    raw_response_path,
                    1 if fake else 0,
                    rb_url,
                    body_top,
                ),
            )
            analysis_id = cursor.lastrowid

            # Insert comments
            for comment in result.comments:
                conn.execute(
                    """
                    INSERT INTO comments (
                        analysis_id, file_path, line_number, message,
                        severity, issue_type, suggestion, diff_context
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_id,
                        comment.file_path,
                        comment.line_number,
                        comment.message,
                        comment.severity.value,
                        comment.issue_type.value,
                        comment.suggestion,
                        comment.diff_context,
                    ),
                )

            return analysis_id

    def ensure_chain_exists(
        self,
        chain_id: str,
        repository: str,
        branch_name: str | None = None,
    ) -> None:
        """Ensure a chain entry exists in the database.

        Creates the chain if it doesn't exist. Used when saving analyses
        one at a time in a chain context.

        Args:
            chain_id: The chain identifier
            repository: Repository name
            branch_name: Optional branch name
        """
        with self._connection() as conn:
            # Check if chain exists
            existing = conn.execute(
                "SELECT 1 FROM chains WHERE chain_id = ?",
                (chain_id,),
            ).fetchone()

            if not existing:
                conn.execute(
                    """
                    INSERT INTO chains (
                        chain_id, created_at, repository, partial,
                        failed_at_rr_id, branch_name
                    ) VALUES (?, ?, ?, 0, NULL, ?)
                    """,
                    (chain_id, datetime.now().isoformat(), repository, branch_name),
                )

    def save_chain(
        self,
        chain_result: ChainReviewResult,
        analysis_method: str,
        model: str,
        diff_infos: dict[int, DiffInfo] | None = None,
        rr_infos: dict[int, ReviewRequestInfo] | None = None,
    ) -> str:
        """Save a chain of analyses to the database.

        Args:
            chain_result: The chain review result to save
            analysis_method: "llm" or "opencode"
            model: Model identifier used for analysis
            diff_infos: Optional dict mapping RR ID to DiffInfo
            rr_infos: Optional dict mapping RR ID to ReviewRequestInfo

        Returns:
            The chain_id
        """
        with self._connection() as conn:
            # Insert chain
            conn.execute(
                """
                INSERT INTO chains (
                    chain_id, created_at, repository, partial,
                    failed_at_rr_id, branch_name
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chain_result.chain_id,
                    datetime.now().isoformat(),
                    chain_result.repository,
                    1 if chain_result.partial else 0,
                    chain_result.failed_at_rr_id,
                    chain_result.branch_name,
                ),
            )

        # Save each analysis in the chain
        for position, review in enumerate(chain_result.reviews, start=1):
            diff_info = diff_infos.get(review.review_request_id) if diff_infos else None
            rr_info = rr_infos.get(review.review_request_id) if rr_infos else None
            self.save_analysis(
                result=review,
                repository=chain_result.repository,
                analysis_method=analysis_method,
                model=model,
                diff_info=diff_info,
                rr_info=rr_info,
                chain_id=chain_result.chain_id,
                chain_position=position,
            )

        return chain_result.chain_id

    def get_analysis(self, analysis_id: int) -> StoredAnalysis | None:
        """Get a single analysis by ID with all comments.

        Args:
            analysis_id: Database ID of the analysis

        Returns:
            StoredAnalysis with comments, or None if not found
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE id = ?",
                (analysis_id,),
            ).fetchone()

            if not row:
                return None

            analysis = self._row_to_analysis(row)

            # Load comments
            comments = conn.execute(
                "SELECT * FROM comments WHERE analysis_id = ? ORDER BY id",
                (analysis_id,),
            ).fetchall()

            analysis.comments = [self._row_to_comment(c) for c in comments]
            return analysis

    def get_analysis_by_rr(
        self, review_request_id: int, diff_revision: int | None = None
    ) -> StoredAnalysis | None:
        """Get the most recent analysis for a review request.

        Args:
            review_request_id: Review Board review request ID
            diff_revision: Optional specific diff revision

        Returns:
            Most recent StoredAnalysis for the RR, or None
        """
        with self._connection() as conn:
            if diff_revision is not None:
                row = conn.execute(
                    """
                    SELECT * FROM analyses
                    WHERE review_request_id = ? AND diff_revision = ?
                    ORDER BY analyzed_at DESC LIMIT 1
                    """,
                    (review_request_id, diff_revision),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM analyses
                    WHERE review_request_id = ?
                    ORDER BY analyzed_at DESC LIMIT 1
                    """,
                    (review_request_id,),
                ).fetchone()

            if not row:
                return None

            analysis = self._row_to_analysis(row)

            # Load comments
            comments = conn.execute(
                "SELECT * FROM comments WHERE analysis_id = ? ORDER BY id",
                (analysis.id,),
            ).fetchall()

            analysis.comments = [self._row_to_comment(c) for c in comments]
            return analysis

    def has_real_analysis(
        self,
        review_request_id: int,
        diff_revision: int,
        analysis_method: str,
    ) -> bool:
        """Check if a non-fake analysis exists for the given RR, diff, and method."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM analyses
                WHERE review_request_id = ? AND diff_revision = ?
                  AND analysis_method = ? AND fake = 0
                LIMIT 1
                """,
                (review_request_id, diff_revision, analysis_method),
            ).fetchone()
            return row is not None

    def list_analyses(
        self,
        review_request_id: int | None = None,
        repository: str | None = None,
        status: str | None = None,
        chain_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[AnalysisListItem]:
        """List analyses with optional filters.

        Args:
            review_request_id: Filter by RR ID
            repository: Filter by repository name
            status: Filter by status (draft, submitted, obsolete, invalid)
            chain_id: Filter by chain ID
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            List of AnalysisListItem objects
        """
        conditions = []
        params = []

        if review_request_id is not None:
            conditions.append("a.review_request_id = ?")
            params.append(review_request_id)
        if repository is not None:
            conditions.append("a.repository = ?")
            params.append(repository)
        if status is not None:
            conditions.append("a.status = ?")
            params.append(status)
        if chain_id is not None:
            conditions.append("a.chain_id = ?")
            params.append(chain_id)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT a.*, COUNT(c.id) as comment_count
                FROM analyses a
                LEFT JOIN comments c ON c.analysis_id = a.id
                WHERE {where_clause}
                GROUP BY a.id
                ORDER BY a.analyzed_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()

            return [self._row_to_list_item(row) for row in rows]

    def get_chain(self, chain_id: str) -> StoredChain | None:
        """Get a chain with all its analyses.

        Args:
            chain_id: The chain identifier

        Returns:
            StoredChain with analyses, or None if not found
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM chains WHERE chain_id = ?",
                (chain_id,),
            ).fetchone()

            if not row:
                return None

            chain = StoredChain(
                chain_id=row["chain_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                repository=row["repository"],
                partial=bool(row["partial"]),
                failed_at_rr_id=row["failed_at_rr_id"],
                branch_name=row["branch_name"],
            )

            # Load analyses in chain order
            analysis_rows = conn.execute(
                """
                SELECT * FROM analyses
                WHERE chain_id = ?
                ORDER BY chain_position
                """,
                (chain_id,),
            ).fetchall()

            for a_row in analysis_rows:
                analysis = self._row_to_analysis(a_row)
                # Load comments for each analysis
                comments = conn.execute(
                    "SELECT * FROM comments WHERE analysis_id = ? ORDER BY id",
                    (analysis.id,),
                ).fetchall()
                analysis.comments = [self._row_to_comment(c) for c in comments]
                chain.analyses.append(analysis)

            return chain

    def mark_submitted(self, analysis_id: int) -> None:
        """Mark an analysis as submitted.

        Args:
            analysis_id: Database ID of the analysis
        """
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE analyses
                SET status = ?, submitted_at = ?
                WHERE id = ?
                """,
                (AnalysisStatus.SUBMITTED.value, datetime.now().isoformat(), analysis_id),
            )

    def mark_obsolete(self, analysis_id: int) -> None:
        """Mark an analysis as obsolete.

        Args:
            analysis_id: Database ID of the analysis
        """
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE analyses SET status = ? WHERE id = ?
                """,
                (AnalysisStatus.OBSOLETE.value, analysis_id),
            )

    def update_status(self, analysis_id: int, status: str) -> None:
        """Update the status of an analysis.

        Args:
            analysis_id: Database ID of the analysis
            status: New status (draft, submitted, obsolete, invalid)
        """
        # Validate status
        try:
            status_enum = AnalysisStatus(status)
        except ValueError:
            valid = [s.value for s in AnalysisStatus]
            raise ValueError(f"Invalid status '{status}'. Must be one of: {valid}") from None

        with self._connection() as conn:
            if status_enum == AnalysisStatus.SUBMITTED:
                conn.execute(
                    """
                    UPDATE analyses
                    SET status = ?, submitted_at = ?
                    WHERE id = ?
                    """,
                    (status, datetime.now().isoformat(), analysis_id),
                )
            else:
                conn.execute(
                    "UPDATE analyses SET status = ? WHERE id = ?",
                    (status, analysis_id),
                )

    def delete_analysis(self, analysis_id: int) -> bool:
        """Delete an analysis and its comments.

        Args:
            analysis_id: Database ID of the analysis to delete

        Returns:
            True if the analysis was deleted, False if not found
        """
        with self._connection() as conn:
            # Check if exists first
            exists = conn.execute("SELECT 1 FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
            if not exists:
                return False

            # Delete comments first (though CASCADE should handle this)
            conn.execute("DELETE FROM comments WHERE analysis_id = ?", (analysis_id,))
            # Delete the analysis
            conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
            return True

    def update_comment(
        self, comment_id: int, message: str | None = None, suggestion: str | None = None
    ) -> bool:
        """Update a comment's message and/or suggestion.

        Args:
            comment_id: Database ID of the comment
            message: New message text (None to keep existing)
            suggestion: New suggestion text (None to keep existing)

        Returns:
            True if the comment was updated, False if not found
        """
        with self._connection() as conn:
            # Check if exists
            exists = conn.execute("SELECT 1 FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if not exists:
                return False

            # Build update query dynamically
            updates = []
            params = []
            if message is not None:
                updates.append("message = ?")
                params.append(message)
            if suggestion is not None:
                updates.append("suggestion = ?")
                params.append(suggestion)

            if not updates:
                return True  # Nothing to update

            params.append(comment_id)
            conn.execute(
                f"UPDATE comments SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            return True

    def update_body_top(self, analysis_id: int, body_top: str) -> bool:
        """Update an analysis's body_top.

        Args:
            analysis_id: Database ID of the analysis
            body_top: New body_top text

        Returns:
            True if the analysis was updated, False if not found
        """
        with self._connection() as conn:
            exists = conn.execute("SELECT 1 FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
            if not exists:
                return False

            conn.execute(
                "UPDATE analyses SET body_top = ? WHERE id = ?",
                (body_top, analysis_id),
            )
            return True

    def get_stats(self) -> DBStats:
        """Get statistics about the database.

        Returns:
            DBStats with counts and recent analyses
        """
        with self._connection() as conn:
            # Total analyses
            total = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]

            # By status
            by_status = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM analyses GROUP BY status"
            ).fetchall():
                by_status[row["status"]] = row["cnt"]

            # By repository
            by_repo = {}
            for row in conn.execute(
                "SELECT repository, COUNT(*) as cnt FROM analyses GROUP BY repository"
            ).fetchall():
                by_repo[row["repository"]] = row["cnt"]

            # By method
            by_method = {}
            for row in conn.execute(
                "SELECT analysis_method, COUNT(*) as cnt FROM analyses GROUP BY analysis_method"
            ).fetchall():
                by_method[row["analysis_method"]] = row["cnt"]

            # Total comments
            total_comments = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]

            # Total chains
            total_chains = conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]

            # Recent analyses
            recent = self.list_analyses(limit=10)

            return DBStats(
                total_analyses=total,
                by_status=by_status,
                by_repository=by_repo,
                by_method=by_method,
                total_comments=total_comments,
                total_chains=total_chains,
                recent_analyses=recent,
            )

    def cleanup(self, older_than_days: int) -> int:
        """Remove analyses older than specified days.

        Args:
            older_than_days: Remove analyses older than this many days

        Returns:
            Number of analyses removed
        """
        cutoff = datetime.now()
        cutoff = cutoff.replace(day=cutoff.day - older_than_days if cutoff.day > older_than_days else 1)
        # Better date calculation
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=older_than_days)

        with self._connection() as conn:
            # Get count before deletion
            count = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE analyzed_at < ?",
                (cutoff.isoformat(),),
            ).fetchone()[0]

            # Delete (comments will be cascade deleted)
            conn.execute(
                "DELETE FROM analyses WHERE analyzed_at < ?",
                (cutoff.isoformat(),),
            )

            # Clean up orphaned chains
            conn.execute(
                """
                DELETE FROM chains
                WHERE chain_id NOT IN (SELECT DISTINCT chain_id FROM analyses WHERE chain_id IS NOT NULL)
                """
            )

            return count

    def delete_fake_analyses(self) -> int:
        """Delete all fake/test analyses.

        Returns:
            Number of analyses deleted
        """
        with self._connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM analyses WHERE fake = 1").fetchone()[0]
            if count > 0:
                conn.execute("DELETE FROM analyses WHERE fake = 1")
            return count

    def _row_to_analysis(self, row: sqlite3.Row) -> StoredAnalysis:
        """Convert a database row to StoredAnalysis."""
        depends_on = []
        if row["depends_on_json"]:
            depends_on = json.loads(row["depends_on_json"])

        # Handle fake column (may not exist in older databases before migration runs)
        try:
            fake = bool(row["fake"])
        except (IndexError, KeyError):
            fake = False

        # Handle rb_url column (may not exist in older databases)
        try:
            rb_url = row["rb_url"]
        except (IndexError, KeyError):
            rb_url = None

        # Handle body_top column (may not exist in older databases)
        try:
            body_top = row["body_top"]
        except (IndexError, KeyError):
            body_top = None

        return StoredAnalysis(
            id=row["id"],
            review_request_id=row["review_request_id"],
            diff_revision=row["diff_revision"],
            repository=row["repository"],
            analyzed_at=datetime.fromisoformat(row["analyzed_at"]),
            summary=row["summary"],
            has_critical_issues=bool(row["has_critical_issues"]),
            status=AnalysisStatus(row["status"]),
            analysis_method=AnalysisMethod(row["analysis_method"]),
            model_used=row["model_used"],
            base_commit_id=row["base_commit_id"],
            target_commit_id=row["target_commit_id"],
            submitter=row["submitter"],
            rr_summary=row["rr_summary"],
            branch=row["branch"],
            depends_on=depends_on,
            chain_id=row["chain_id"],
            chain_position=row["chain_position"],
            submitted_at=(datetime.fromisoformat(row["submitted_at"]) if row["submitted_at"] else None),
            raw_response_path=row["raw_response_path"],
            fake=fake,
            rb_url=rb_url,
            body_top=body_top,
        )

    def _row_to_comment(self, row: sqlite3.Row) -> StoredComment:
        """Convert a database row to StoredComment."""
        # Handle diff_context column (may not exist in older databases)
        try:
            diff_context = row["diff_context"]
        except (IndexError, KeyError):
            diff_context = None

        return StoredComment(
            id=row["id"],
            analysis_id=row["analysis_id"],
            file_path=row["file_path"],
            line_number=row["line_number"],
            message=row["message"],
            severity=row["severity"],
            issue_type=row["issue_type"],
            suggestion=row["suggestion"],
            diff_context=diff_context,
        )

    def _row_to_list_item(self, row: sqlite3.Row) -> AnalysisListItem:
        """Convert a database row to AnalysisListItem."""
        # Handle fake column (may not exist in older databases before migration runs)
        try:
            fake = bool(row["fake"])
        except (IndexError, KeyError):
            fake = False

        # Handle rb_url column (may not exist in older databases)
        try:
            rb_url = row["rb_url"]
        except (IndexError, KeyError):
            rb_url = None

        return AnalysisListItem(
            id=row["id"],
            review_request_id=row["review_request_id"],
            diff_revision=row["diff_revision"],
            repository=row["repository"],
            analyzed_at=datetime.fromisoformat(row["analyzed_at"]),
            status=AnalysisStatus(row["status"]),
            analysis_method=AnalysisMethod(row["analysis_method"]),
            model_used=row["model_used"],
            summary=row["summary"],
            issue_count=row["comment_count"],
            has_critical_issues=bool(row["has_critical_issues"]),
            chain_id=row["chain_id"],
            rr_summary=row["rr_summary"],
            fake=fake,
            rb_url=rb_url,
        )
