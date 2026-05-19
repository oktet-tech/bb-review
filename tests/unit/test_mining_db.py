"""Tests for the rules-mining cache database."""

from pathlib import Path
import sqlite3

from bb_review.db.mining_db import MiningDatabase


def test_mining_db_creates_tables(tmp_path: Path):
    db_path = tmp_path / "rules_mining.db"
    MiningDatabase(db_path)

    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    assert "mined_review_requests" in tables
    assert "mined_comments" in tables
