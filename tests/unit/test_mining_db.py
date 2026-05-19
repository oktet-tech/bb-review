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
