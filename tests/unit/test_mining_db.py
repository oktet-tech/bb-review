"""Tests for the rules-mining cache database."""

from pathlib import Path
import sqlite3

from bb_review.db.mining_db import MiningDatabase
from bb_review.triage.models import RBComment


def _sample_comment() -> RBComment:
    return RBComment(
        review_id=5,
        comment_id=9,
        reviewer="alice",
        text="fix the lock ordering",
        file_path="src/a.c",
        line_number=12,
        issue_opened=True,
        issue_status="resolved",
    )


def test_mining_db_creates_tables(tmp_path: Path):
    db_path = tmp_path / "rules_mining.db"
    MiningDatabase(db_path)

    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    assert "mined_review_requests" in tables
    assert "mined_comments" in tables


def test_record_and_has_review_request(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    assert db.has_review_request(100) is False

    db.record_review_request(
        rr_id=100,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="Add widget",
        submitter="bob",
        branch="main",
        rb_last_updated="2026-05-10",
        comments=[_sample_comment()],
    )
    assert db.has_review_request(100) is True


def test_record_review_request_is_idempotent(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    for _ in range(2):
        db.record_review_request(
            rr_id=100,
            repository="testrepo",
            rr_status="submitted",
            rr_summary="Add widget",
            submitter="bob",
            branch="main",
            rb_last_updated="2026-05-10",
            comments=[_sample_comment()],
        )
    stats = db.get_repo_stats("testrepo")
    assert stats.review_request_count == 1
    assert stats.comment_count == 1


def test_get_comments_for_repo(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=100,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="Add widget",
        submitter="bob",
        branch="main",
        rb_last_updated="2026-05-10",
        comments=[_sample_comment()],
    )
    comments = db.get_comments_for_repo("testrepo")
    assert len(comments) == 1
    assert comments[0].rr_id == 100
    assert comments[0].rr_status == "submitted"
    assert comments[0].issue_status == "resolved"
    assert comments[0].is_body_comment is False
    assert comments[0].issue_opened is True

    assert db.get_comments_for_repo("other") == []


def test_get_repo_stats(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=100,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="Add widget",
        submitter="bob",
        branch="main",
        rb_last_updated="2026-05-10",
        comments=[_sample_comment()],
    )
    stats = db.get_repo_stats("testrepo")
    assert stats.repository == "testrepo"
    assert stats.review_request_count == 1
    assert stats.comment_count == 1


def test_diff_hunk_round_trips_through_record_and_read(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=100,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="s",
        submitter="bob",
        branch="main",
        rb_last_updated="2026-05-10",
        comments=[
            RBComment(
                review_id=5,
                comment_id=9,
                reviewer="alice",
                text="fix",
                file_path="src/a.c",
                line_number=12,
                diff_revision=3,
                diff_hunk="@@ -10,3 +10,4 @@\n a\n-b\n+B\n c",
            ),
        ],
    )
    comments = db.get_comments_for_repo("testrepo")
    assert len(comments) == 1
    assert comments[0].diff_revision == 3
    assert comments[0].diff_hunk == "@@ -10,3 +10,4 @@\n a\n-b\n+B\n c"


def test_schema_migration_adds_diff_columns(tmp_path: Path):
    """An older DB (without diff_revision/diff_hunk) gains the columns
    on next open, without losing existing data."""
    db_path = tmp_path / "m.db"
    # Create a DB with the pre-migration schema and one row.
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE mined_review_requests (
            rr_id INTEGER PRIMARY KEY,
            repository TEXT NOT NULL,
            rr_status TEXT NOT NULL,
            rr_summary TEXT,
            submitter TEXT,
            branch TEXT,
            rb_last_updated TEXT,
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE mined_comments (
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
        """
    )
    conn.execute(
        "INSERT INTO mined_review_requests "
        "(rr_id, repository, rr_status, fetched_at) VALUES (1, 'r', 'submitted', 'now')"
    )
    conn.execute(
        "INSERT INTO mined_comments (rr_id, review_id, comment_id, text) VALUES (1, 2, 3, 'existing comment')"
    )
    conn.commit()
    conn.close()

    # Re-opening with current MiningDatabase must add the new columns.
    MiningDatabase(db_path)

    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(mined_comments)")}
    assert "diff_revision" in cols
    assert "diff_hunk" in cols
    # Existing row is intact.
    row = conn.execute("SELECT text, diff_hunk FROM mined_comments WHERE id = 1").fetchone()
    conn.close()
    assert row[0] == "existing comment"
    assert row[1] is None
