# Rules-Mining Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `rules` CLI command group that mines human reviewer comments from Review Board into a cache database, then runs an agent to draft a candidate rules file for a repo's review guide.

**Architecture:** Two-step pipeline. `rules fetch` discovers recent submitted/discarded RRs for a repo, pulls reviewer comments via the existing `RBCommentFetcher`, and upserts them into a dedicated `rules_mining.db`. `rules draft` loads the cached comments, runs a Claude/Codex agent (with the repo checked out so it can read code), and writes `guides/{repo}/draft-rules.md`. The split keeps the slow RB fetch separate from the iterable synthesis step.

**Tech Stack:** Python 3.10+, Click, SQLite (`sqlite3` stdlib), pytest, `uv` for tooling. Reuses `RBCommentFetcher`, `RepoManager`, the LLM/agent provider config, and the existing reviewer subprocess plumbing.

Reference spec: `docs/superpowers/specs/2026-05-19-rules-mining-command-design.md`.

---

## File Structure

**Create:**
- `bb_review/db/mining_db.py` — `MiningDatabase` class + `MinedReviewRequest`, `MinedComment`, `RepoMiningStats` dataclasses. The cache DB layer.
- `bb_review/rules/__init__.py` — package marker for rules-mining logic.
- `bb_review/rules/fetcher.py` — `fetch_repo_rules_data()`: orchestrates RR discovery + comment fetch + upsert.
- `bb_review/rules/synthesizer.py` — `format_comments_artifact()`, `build_rules_prompt()`, `draft_rules()`, `RulesDraftError`.
- `bb_review/rules/agent_runner.py` — `run_agent()`: generic Claude/Codex CLI runner returning text, `AgentRunError`.
- `bb_review/cli/rules.py` — the `rules` Click group: `fetch`, `draft`, `show`.

**Modify:**
- `bb_review/rr/rb_client.py` — add `list_repo_review_requests()`.
- `bb_review/cli/__init__.py` — register the `rules` submodule.
- `tests/mocks/rb_client.py` — add `list_repo_review_requests()` to `MockRBClient`.

**Test:**
- `tests/unit/test_mining_db.py`
- `tests/unit/test_rb_client.py`
- `tests/unit/test_rules_fetcher.py`
- `tests/unit/test_rules_synthesizer.py`
- `tests/unit/test_agent_runner.py`
- `tests/cli/test_rules.py`

---

## Task 1: MiningDatabase schema and dataclasses

**Files:**
- Create: `bb_review/db/mining_db.py`
- Test: `tests/unit/test_mining_db.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mining_db.py`:

```python
"""Tests for the rules-mining cache database."""

import sqlite3
from pathlib import Path

from bb_review.db.mining_db import MiningDatabase
from bb_review.triage.models import RBComment


def test_mining_db_creates_tables(tmp_path: Path):
    db_path = tmp_path / "rules_mining.db"
    MiningDatabase(db_path)

    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    assert "mined_review_requests" in tables
    assert "mined_comments" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mining_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bb_review.db.mining_db'`

- [ ] **Step 3: Write minimal implementation**

Create `bb_review/db/mining_db.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_mining_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bb_review/db/mining_db.py tests/unit/test_mining_db.py
git commit -m "feat: add MiningDatabase schema for rules-mining cache"
```

---

## Task 2: MiningDatabase write methods

**Files:**
- Modify: `bb_review/db/mining_db.py` (add methods to `MiningDatabase`)
- Test: `tests/unit/test_mining_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mining_db.py`:

```python
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
```

Note: `get_repo_stats` is implemented in Task 3. This test file will not fully pass until Task 3; Step 2 below confirms the Task 2 methods exist.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mining_db.py::test_record_and_has_review_request -v`
Expected: FAIL with `AttributeError: 'MiningDatabase' object has no attribute 'has_review_request'`

- [ ] **Step 3: Write minimal implementation**

Add these methods to the `MiningDatabase` class in `bb_review/db/mining_db.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_mining_db.py::test_record_and_has_review_request -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bb_review/db/mining_db.py tests/unit/test_mining_db.py
git commit -m "feat: add MiningDatabase write methods for cached RRs"
```

---

## Task 3: MiningDatabase read methods

**Files:**
- Modify: `bb_review/db/mining_db.py` (add methods to `MiningDatabase`)
- Test: `tests/unit/test_mining_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mining_db.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mining_db.py::test_get_repo_stats -v`
Expected: FAIL with `AttributeError: 'MiningDatabase' object has no attribute 'get_repo_stats'`

- [ ] **Step 3: Write minimal implementation**

Add these methods to the `MiningDatabase` class in `bb_review/db/mining_db.py`:

```python
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
```

- [ ] **Step 4: Run all mining_db tests to verify they pass**

Run: `uv run pytest tests/unit/test_mining_db.py -v`
Expected: PASS (all 5 tests, including the Task 2 idempotency test that needed `get_repo_stats`)

- [ ] **Step 5: Commit**

```bash
git add bb_review/db/mining_db.py tests/unit/test_mining_db.py
git commit -m "feat: add MiningDatabase read methods for comments and stats"
```

---

## Task 4: rb_client.list_repo_review_requests

**Files:**
- Modify: `bb_review/rr/rb_client.py` (add method to `ReviewBoardClient`)
- Test: `tests/unit/test_rb_client.py`

`datetime` and `timedelta` are already imported at the top of `rb_client.py` (used by `get_recent_reviews`). The new method goes right after `get_recent_reviews`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_rb_client.py`:

```python
"""Tests for ReviewBoardClient methods that do not need a live server."""

from bb_review.rr.rb_client import ReviewBoardClient


def test_list_repo_review_requests_merges_statuses(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    def fake_api_get(path: str, params: dict | None = None) -> dict:
        status = params["status"]
        if status == "submitted":
            return {
                "review_requests": [
                    {"id": 1, "last_updated": "2026-05-10T00:00:00"},
                    {"id": 2, "last_updated": "2026-05-15T00:00:00"},
                ]
            }
        return {
            "review_requests": [
                {"id": 2, "last_updated": "2026-05-15T00:00:00"},
                {"id": 3, "last_updated": "2026-05-01T00:00:00"},
            ]
        }

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    result = client.list_repo_review_requests(
        repository="test-repo",
        statuses=["submitted", "discarded"],
        limit=10,
    )
    # Deduped by id, sorted by last_updated descending.
    assert [r["id"] for r in result] == [2, 1, 3]


def test_list_repo_review_requests_respects_limit(monkeypatch):
    client = ReviewBoardClient(url="https://rb.example.com", bot_username="bot")

    def fake_api_get(path: str, params: dict | None = None) -> dict:
        return {
            "review_requests": [
                {"id": 1, "last_updated": "2026-05-10T00:00:00"},
                {"id": 2, "last_updated": "2026-05-15T00:00:00"},
                {"id": 3, "last_updated": "2026-05-20T00:00:00"},
            ]
        }

    monkeypatch.setattr(client, "_api_get", fake_api_get)

    result = client.list_repo_review_requests(
        repository="test-repo",
        statuses=["submitted"],
        limit=2,
    )
    assert [r["id"] for r in result] == [3, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rb_client.py -v`
Expected: FAIL with `AttributeError: 'ReviewBoardClient' object has no attribute 'list_repo_review_requests'`

- [ ] **Step 3: Write minimal implementation**

Add this method to the `ReviewBoardClient` class in `bb_review/rr/rb_client.py`, immediately after the `get_recent_reviews` method:

```python
    def list_repo_review_requests(
        self,
        repository: str,
        statuses: list[str],
        limit: int = 50,
        days: int = 0,
    ) -> list[dict]:
        """List review requests for a repository, filtered by status.

        RB's `status` query parameter accepts a single value, so each
        status is queried separately and the results are merged,
        de-duplicated by id, and sorted by last_updated descending.

        Args:
            repository: RB repository name or id.
            statuses: Statuses to include, e.g. ["submitted", "discarded"].
            limit: Max number of review requests to return.
            days: If > 0, only include RRs updated within this many days.

        Returns:
            Review request dicts, newest first, capped at `limit`.
        """
        merged: dict[int, dict] = {}
        for status in statuses:
            params: dict[str, str] = {
                "repository": repository,
                "status": status,
                "max-results": str(limit),
            }
            if days > 0:
                cutoff = (datetime.now() - timedelta(days=days)).strftime(
                    "%Y-%m-%dT00:00:00"
                )
                params["last-updated-from"] = cutoff

            result = self._api_get("/api/review-requests/", params)
            for rr in result.get("review_requests", []):
                merged[rr["id"]] = rr

        ordered = sorted(
            merged.values(),
            key=lambda r: r.get("last_updated", ""),
            reverse=True,
        )
        return ordered[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rb_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bb_review/rr/rb_client.py tests/unit/test_rb_client.py
git commit -m "feat: add list_repo_review_requests to ReviewBoardClient"
```

---

## Task 5: Rules fetcher orchestration

**Files:**
- Create: `bb_review/rules/__init__.py`
- Create: `bb_review/rules/fetcher.py`
- Modify: `tests/mocks/rb_client.py` (add `list_repo_review_requests` to `MockRBClient`)
- Test: `tests/unit/test_rules_fetcher.py`

- [ ] **Step 1: Extend MockRBClient**

In `tests/mocks/rb_client.py`, add a `repo_review_requests` parameter to `MockRBClient.__init__`. Change the `__init__` signature and body:

Find:
```python
    def __init__(
        self,
        reviews: dict[int, dict] | None = None,
        diffs: dict[int, MockDiffInfo] | None = None,
        repositories: dict[int, dict] | None = None,
        review_request_infos: dict[int, ReviewRequestInfo] | None = None,
    ):
```

Replace with:
```python
    def __init__(
        self,
        reviews: dict[int, dict] | None = None,
        diffs: dict[int, MockDiffInfo] | None = None,
        repositories: dict[int, dict] | None = None,
        review_request_infos: dict[int, ReviewRequestInfo] | None = None,
        repo_review_requests: list[dict] | None = None,
    ):
```

Then find the end of `__init__` (the line `self._connected = False`) and add after it:
```python
        self.repo_review_requests = repo_review_requests or []
```

Add this method to `MockRBClient` (place it just before the `reset` method):
```python
    def list_repo_review_requests(
        self,
        repository: str,
        statuses: list[str],
        limit: int = 50,
        days: int = 0,
    ) -> list[dict]:
        """Return the configured review request dicts, capped at `limit`."""
        return self.repo_review_requests[:limit]
```

- [ ] **Step 2: Write the failing test**

Create `bb_review/rules/__init__.py`:
```python
"""Rules-mining: cache reviewer comments and draft repo review rules."""
```

Create `tests/unit/test_rules_fetcher.py`:
```python
"""Tests for the rules-mining fetch orchestration."""

from pathlib import Path

from bb_review.db.mining_db import MiningDatabase
from bb_review.rules.fetcher import fetch_repo_rules_data
from bb_review.triage.models import RBComment
from tests.mocks import MockRBClient


class FakeCommentFetcher:
    """Stand-in for RBCommentFetcher with canned per-RR comments."""

    def __init__(self, comments_by_rr: dict[int, list[RBComment]], fail_on: int | None = None):
        self.comments_by_rr = comments_by_rr
        self.fail_on = fail_on

    def fetch_all_comments(self, rr_id: int) -> list[RBComment]:
        if self.fail_on is not None and rr_id == self.fail_on:
            raise RuntimeError("simulated fetch failure")
        return self.comments_by_rr.get(rr_id, [])


def _rr(rr_id: int, status: str = "submitted") -> dict:
    return {
        "id": rr_id,
        "summary": f"RR {rr_id}",
        "status": status,
        "last_updated": "2026-05-10T00:00:00",
        "branch": "main",
        "links": {"submitter": {"title": "alice"}},
    }


def test_fetch_records_new_and_skips_cached(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=1,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="old",
        submitter="x",
        branch="main",
        rb_last_updated="d",
        comments=[],
    )
    rb = MockRBClient(repo_review_requests=[_rr(1), _rr(2, "discarded")])
    fetcher = FakeCommentFetcher(
        {2: [RBComment(review_id=7, comment_id=8, reviewer="bob", text="nit")]}
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="testrepo",
        rb_repo_name="test-repo",
        bot_username="bot",
        count=30,
        comment_fetcher=fetcher,
    )

    assert counts["total"] == 2
    assert counts["fetched"] == 1
    assert counts["skipped"] == 1
    assert counts["comments"] == 1
    assert db.has_review_request(2) is True


def test_fetch_refresh_re_fetches_cached(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=1,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="old",
        submitter="x",
        branch="main",
        rb_last_updated="d",
        comments=[],
    )
    rb = MockRBClient(repo_review_requests=[_rr(1)])
    fetcher = FakeCommentFetcher(
        {1: [RBComment(review_id=3, comment_id=4, reviewer="bob", text="re-fetched")]}
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="testrepo",
        rb_repo_name="test-repo",
        bot_username="bot",
        count=30,
        refresh=True,
        comment_fetcher=fetcher,
    )

    assert counts["fetched"] == 1
    assert counts["skipped"] == 0
    assert db.get_repo_stats("testrepo").comment_count == 1


def test_fetch_continues_after_per_rr_error(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    rb = MockRBClient(repo_review_requests=[_rr(1), _rr(2)])
    fetcher = FakeCommentFetcher(
        {2: [RBComment(review_id=9, comment_id=10, reviewer="c", text="ok")]},
        fail_on=1,
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="testrepo",
        rb_repo_name="test-repo",
        bot_username="bot",
        count=30,
        comment_fetcher=fetcher,
    )

    assert counts["total"] == 2
    assert counts["fetched"] == 1
    assert db.has_review_request(1) is False
    assert db.has_review_request(2) is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rules_fetcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bb_review.rules.fetcher'`

- [ ] **Step 4: Write minimal implementation**

Create `bb_review/rules/fetcher.py`:
```python
"""Fetch reviewer comments from Review Board into the mining cache."""

from collections.abc import Callable
import logging

from ..db.mining_db import MiningDatabase
from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher


logger = logging.getLogger(__name__)

# RR statuses worth mining: both carry full human review history.
MINED_STATUSES = ["submitted", "discarded"]


def _extract_submitter(rr: dict) -> str:
    """Pull the submitter username from an RB review-request dict."""
    return rr.get("links", {}).get("submitter", {}).get("title", "")


def fetch_repo_rules_data(
    rb_client: ReviewBoardClient,
    mining_db: MiningDatabase,
    repo_name: str,
    rb_repo_name: str,
    bot_username: str,
    count: int,
    days: int = 0,
    refresh: bool = False,
    on_progress: Callable[[int, int, int], None] | None = None,
    comment_fetcher: RBCommentFetcher | None = None,
) -> dict[str, int]:
    """Fetch reviewer comments for the most recent RRs of a repository.

    Args:
        rb_client: Connected Review Board client.
        mining_db: Cache database to upsert into.
        repo_name: Config repository name; stored as `repository` in the cache.
        rb_repo_name: Review Board repository name used for the RB query.
        bot_username: Bot account whose comments are excluded.
        count: Max number of recent RRs to mine.
        days: If > 0, only consider RRs updated within this many days.
        refresh: If True, re-fetch RRs even if already cached.
        on_progress: Called with (current, total, comment_count) per RR.
        comment_fetcher: Override for the RBCommentFetcher (used in tests).

    Returns:
        Counts dict with keys: total, fetched, skipped, comments.
    """
    review_requests = rb_client.list_repo_review_requests(
        repository=rb_repo_name,
        statuses=MINED_STATUSES,
        limit=count,
        days=days,
    )
    if comment_fetcher is None:
        comment_fetcher = RBCommentFetcher(rb_client, bot_username)

    total = len(review_requests)
    fetched = 0
    skipped = 0
    comment_total = 0

    for i, rr in enumerate(review_requests):
        rr_id = rr["id"]

        if not refresh and mining_db.has_review_request(rr_id):
            skipped += 1
            if on_progress:
                on_progress(i + 1, total, 0)
            continue

        try:
            comments = comment_fetcher.fetch_all_comments(rr_id)
        except Exception as e:
            logger.warning(f"Failed to fetch comments for RR #{rr_id}: {e}")
            if on_progress:
                on_progress(i + 1, total, 0)
            continue

        mining_db.record_review_request(
            rr_id=rr_id,
            repository=repo_name,
            rr_status=rr.get("status", ""),
            rr_summary=rr.get("summary", ""),
            submitter=_extract_submitter(rr),
            branch=rr.get("branch", "") or "",
            rb_last_updated=rr.get("last_updated", "") or "",
            comments=comments,
        )
        fetched += 1
        comment_total += len(comments)
        if on_progress:
            on_progress(i + 1, total, len(comments))

    return {
        "total": total,
        "fetched": fetched,
        "skipped": skipped,
        "comments": comment_total,
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rules_fetcher.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add bb_review/rules/__init__.py bb_review/rules/fetcher.py tests/mocks/rb_client.py tests/unit/test_rules_fetcher.py
git commit -m "feat: add rules-mining fetch orchestration"
```

---

## Task 6: Comment artifact formatting and synthesis prompt

**Files:**
- Create: `bb_review/rules/synthesizer.py`
- Test: `tests/unit/test_rules_synthesizer.py`

This task creates `synthesizer.py` with the two pure functions. Task 8 adds `draft_rules` to the same file.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_rules_synthesizer.py`:
```python
"""Tests for the rules-mining synthesis helpers."""

from bb_review.db.mining_db import MinedComment
from bb_review.rules.synthesizer import build_rules_prompt, format_comments_artifact


def _comment(**kw) -> MinedComment:
    defaults = dict(
        rr_id=1,
        rr_status="submitted",
        review_id=10,
        comment_id=20,
        reviewer="alice",
        text="check the return value",
        file_path="src/a.c",
        line_number=42,
        is_body_comment=False,
        issue_opened=True,
        issue_status="resolved",
        reply_to_id=None,
    )
    defaults.update(kw)
    return MinedComment(**defaults)


def test_format_comments_artifact_groups_by_file():
    comments = [
        _comment(file_path="src/a.c", text="comment one"),
        _comment(file_path="src/b.c", text="comment two"),
    ]
    artifact = format_comments_artifact(comments)
    assert "## src/a.c" in artifact
    assert "## src/b.c" in artifact
    assert "comment one" in artifact
    assert "comment two" in artifact
    assert "Total comments: 2" in artifact


def test_format_comments_artifact_tags_status_and_rr():
    artifact = format_comments_artifact([_comment(rr_id=77, issue_status="dropped")])
    assert "RR #77" in artifact
    assert "dropped" in artifact
    assert "reviewer: alice" in artifact


def test_format_comments_artifact_handles_body_comments():
    artifact = format_comments_artifact(
        [_comment(file_path=None, is_body_comment=True, text="overall looks fine")]
    )
    assert "(general / body comments)" in artifact
    assert "overall looks fine" in artifact


def test_build_rules_prompt_includes_repo_and_sections():
    prompt = build_rules_prompt("myrepo", "ARTIFACT TEXT", existing_patterns=None)
    assert "myrepo" in prompt
    assert "Recurring Mistakes" in prompt
    assert "False-Positive Candidates" in prompt
    assert ".bb_review_mined_comments.md" in prompt


def test_build_rules_prompt_includes_existing_patterns():
    prompt = build_rules_prompt(
        "myrepo", "ARTIFACT", existing_patterns="EXISTING RULES BLOCK"
    )
    assert "EXISTING RULES BLOCK" in prompt
    assert "only output rules that are NEW" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rules_synthesizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bb_review.rules.synthesizer'`

- [ ] **Step 3: Write minimal implementation**

Create `bb_review/rules/synthesizer.py`:
```python
"""Synthesize cached reviewer comments into a draft rules document."""

from ..db.mining_db import MinedComment


def format_comments_artifact(comments: list[MinedComment]) -> str:
    """Render cached comments as a markdown artifact for the agent.

    Comments are grouped by file so recurring per-file themes are visible.
    Each entry is tagged with its RR, RR status, reviewer, and issue
    status, which the synthesis prompt uses for weighting.
    """
    by_file: dict[str, list[MinedComment]] = {}
    for c in comments:
        key = c.file_path or "(general / body comments)"
        by_file.setdefault(key, []).append(c)

    lines: list[str] = ["# Mined Reviewer Comments", ""]
    lines.append(f"Total comments: {len(comments)}")
    lines.append("")

    for file_path in sorted(by_file):
        lines.append(f"## {file_path}")
        lines.append("")
        for c in by_file[file_path]:
            loc = f":{c.line_number}" if c.line_number else ""
            status = c.issue_status or ("issue" if c.issue_opened else "comment")
            lines.append(
                f"- [RR #{c.rr_id} | {c.rr_status} | reviewer: {c.reviewer} "
                f"| {status}] {file_path}{loc}"
            )
            body = c.text.strip().replace("\n", "\n  ")
            lines.append(f"  {body}")
        lines.append("")

    return "\n".join(lines)


def build_rules_prompt(
    repo_name: str,
    comments_artifact: str,
    existing_patterns: str | None,
) -> str:
    """Build the agent prompt for drafting repo review rules.

    Note: `comments_artifact` is included for callers that want it; the
    agent reads the same content from `.bb_review_mined_comments.md` in
    its working directory, which `draft_rules` writes before launch.
    """
    prompt = f"""You are drafting a code-review rules document for the \
repository `{repo_name}`.

You are given a collection of real comments that human reviewers left on \
past review requests for this repository. They are written to the file \
`.bb_review_mined_comments.md` in your current working directory -- read \
it first. The repository source code is checked out in the same directory, \
so you may open and read files to ground and verify the rules you write.

How to interpret the comments:
- Each comment is tagged with its review request, the RR status, the \
reviewer, and an issue status.
- `issue status = resolved` -> the author agreed and fixed it. These are \
confirmed mistakes and are strong rule candidates.
- `issue status = dropped` -> the author pushed back or disagreed. Treat \
these as weak signals and as false-positive candidates.
- A pattern that recurs across multiple distinct RRs matters more than a \
one-off remark.

Produce a Markdown document with these sections:
1. `# Draft Review Rules: {repo_name}` -- the title.
2. `## Recurring Mistakes` -- concrete mistakes reviewers repeatedly flag, \
each a bullet with a short rationale, ordered by how often they recur.
3. `## Conventions & Patterns` -- coding conventions and expected patterns \
the comments reveal.
4. `## False-Positive Candidates` -- patterns drawn from `dropped` issues \
that look like problems but reviewers considered acceptable.

For each rule, prefer concrete, checkable statements over vague advice. \
Where a comment references a specific file, open it to confirm the rule is \
accurate before including it.
"""

    if existing_patterns:
        prompt += f"""
An existing `technical-patterns.md` already documents rules for this repo. \
Do NOT repeat anything already covered there -- only output rules that are \
NEW relative to it:

<existing-technical-patterns>
{existing_patterns}
</existing-technical-patterns>
"""

    prompt += """
Output ONLY the Markdown document. Do not include narration, thinking, or \
commentary about your process.
"""
    return prompt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rules_synthesizer.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add bb_review/rules/synthesizer.py tests/unit/test_rules_synthesizer.py
git commit -m "feat: add comment-artifact formatting and rules prompt builder"
```

---

## Task 7: Generic agent runner

**Files:**
- Create: `bb_review/rules/agent_runner.py`
- Test: `tests/unit/test_agent_runner.py`

This reuses `find_claude_binary` and `find_codex_binary` from the existing reviewers. The subprocess paths are not unit-tested (they shell out to real CLIs, matching how `reviewers/claude_code.py` is tested); only the dispatch guard is tested.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_agent_runner.py`:
```python
"""Tests for the generic agent runner."""

from pathlib import Path

import pytest

from bb_review.rules.agent_runner import AgentRunError, run_agent


def test_run_agent_rejects_unknown_method(tmp_path: Path):
    with pytest.raises(AgentRunError, match="Unknown agent method"):
        run_agent(method="bogus", repo_path=tmp_path, prompt="hi")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_agent_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bb_review.rules.agent_runner'`

- [ ] **Step 3: Write minimal implementation**

Create `bb_review/rules/agent_runner.py`:
```python
"""Generic agent CLI runner that returns plain text output.

Reuses the binary-discovery helpers from the review reviewers but runs the
agent without the review-specific patch-file lifecycle or output parsing.
"""

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from ..reviewers.claude_code import find_claude_binary
from ..reviewers.codex import find_codex_binary


logger = logging.getLogger(__name__)


class AgentRunError(Exception):
    """Error running an agent CLI."""


def run_agent(
    method: str,
    repo_path: Path,
    prompt: str,
    model: str | None = None,
    timeout: int = 600,
    binary_path: str | None = None,
    transcript_path: Path | None = None,
) -> str:
    """Run an agent CLI in `repo_path` and return its final text output.

    Args:
        method: 'claude' or 'codex'.
        repo_path: Working directory for the agent (a repo checkout).
        prompt: Prompt text, passed on stdin.
        model: Optional model override.
        timeout: Timeout in seconds.
        binary_path: Optional explicit binary path.
        transcript_path: If set, the raw agent output is saved here.

    Returns:
        The agent's final text output.

    Raises:
        AgentRunError: For unknown method, non-zero exit, timeout, or empty
            output.
    """
    if method == "claude":
        return _run_claude(
            repo_path, prompt, model, timeout, binary_path or "claude", transcript_path
        )
    if method == "codex":
        return _run_codex(
            repo_path, prompt, model, timeout, binary_path or "codex", transcript_path
        )
    raise AgentRunError(
        f"Unknown agent method: {method!r} (expected 'claude' or 'codex')"
    )


def _run_claude(
    repo_path: Path,
    prompt: str,
    model: str | None,
    timeout: int,
    binary_path: str,
    transcript_path: Path | None,
) -> str:
    """Run Claude Code in headless mode and return its result text."""
    claude_bin = find_claude_binary(binary_path)
    cmd = [
        claude_bin,
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        "40",
        "--allowedTools",
        "Read,Grep,Glob,Bash",
    ]
    if model:
        cmd.extend(["--model", model])
    if transcript_path:
        cmd.append("--verbose")

    logger.info(f"Running Claude Code in {repo_path}")
    print(f"  Command: {' '.join(cmd)}", file=sys.stderr)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise AgentRunError(f"Claude Code timed out after {timeout}s") from e

    if result.returncode != 0:
        raise AgentRunError(
            f"Claude Code exited with code {result.returncode}: "
            f"{result.stderr or result.stdout or 'unknown error'}"
        )

    output = result.stdout.strip()
    if not output:
        raise AgentRunError("Claude Code returned empty output")
    if transcript_path:
        transcript_path.write_text(output)

    try:
        envelope = json.loads(output)
        if isinstance(envelope, list):
            envelope = envelope[-1] if envelope else {}
        text = envelope.get("result", "")
    except json.JSONDecodeError as e:
        raise AgentRunError(f"Failed to parse Claude Code JSON output: {e}") from e

    if not text:
        raise AgentRunError("Claude Code produced no result text")
    return text


def _run_codex(
    repo_path: Path,
    prompt: str,
    model: str | None,
    timeout: int,
    binary_path: str,
    transcript_path: Path | None,
) -> str:
    """Run Codex in read-only sandbox and return its last message."""
    codex_bin = find_codex_binary(binary_path)
    fd, output_path = tempfile.mkstemp(suffix=".txt", prefix="bb_review_rules_codex_")
    os.close(fd)

    try:
        cmd = [codex_bin, "exec", "-s", "read-only", "-o", output_path]
        if model:
            cmd.extend(["-m", model])
        if transcript_path:
            cmd.append("--json")
        cmd.append("-")

        logger.info(f"Running Codex in {repo_path}")
        print(f"  Command: {' '.join(cmd)}", file=sys.stderr)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(repo_path),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise AgentRunError(f"Codex timed out after {timeout}s") from e

        if result.returncode != 0:
            raise AgentRunError(
                f"Codex exited with code {result.returncode}: "
                f"{result.stderr or result.stdout or 'unknown error'}"
            )

        if transcript_path and result.stdout:
            transcript_path.write_text(result.stdout)

        out_file = Path(output_path)
        output = out_file.read_text().strip() if out_file.exists() else ""
        if not output:
            output = result.stdout.strip()
        if not output:
            raise AgentRunError("Codex returned empty output")
        return output
    finally:
        Path(output_path).unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_agent_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bb_review/rules/agent_runner.py tests/unit/test_agent_runner.py
git commit -m "feat: add generic Claude/Codex agent runner"
```

---

## Task 8: draft_rules orchestration

**Files:**
- Modify: `bb_review/rules/synthesizer.py` (add `RulesDraftError` and `draft_rules`)
- Test: `tests/unit/test_rules_synthesizer.py`

- [ ] **Step 1: Update the imports in the test file**

Replace the import block at the top of `tests/unit/test_rules_synthesizer.py`:
```python
"""Tests for the rules-mining synthesis helpers."""

from bb_review.db.mining_db import MinedComment
from bb_review.rules.synthesizer import build_rules_prompt, format_comments_artifact
```

with this expanded block (Task 8 adds `draft_rules` tests that need more imports):
```python
"""Tests for the rules-mining synthesis helpers."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bb_review.db.mining_db import MinedComment, MiningDatabase
from bb_review.rules.synthesizer import (
    RulesDraftError,
    build_rules_prompt,
    draft_rules,
    format_comments_artifact,
)
from bb_review.triage.models import RBComment
```

- [ ] **Step 2: Append the failing tests**

Append to `tests/unit/test_rules_synthesizer.py` (no import lines — they were added in Step 1):
```python
class FakeRepoManager:
    """Minimal RepoManager stand-in for draft_rules tests."""

    def __init__(self, repo_path: Path, default_branch: str = "main"):
        self._repo_path = repo_path
        self._default_branch = default_branch
        self.checked_out: list[str] = []

    def ensure_clone(self, name: str) -> None:
        return None

    def get_repo(self, name: str):
        return SimpleNamespace(default_branch=self._default_branch)

    def checkout(self, name: str, ref: str) -> None:
        self.checked_out.append(ref)

    def get_local_path(self, name: str) -> Path:
        return self._repo_path


def _seed_db(tmp_path: Path) -> MiningDatabase:
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=1,
        repository="myrepo",
        rr_status="submitted",
        rr_summary="s",
        submitter="bob",
        branch="main",
        rb_last_updated="d",
        comments=[RBComment(review_id=2, comment_id=3, reviewer="a", text="bug here")],
    )
    return db


def test_draft_rules_raises_when_no_comments(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    repo_path = tmp_path / "checkout"
    repo_path.mkdir()

    with pytest.raises(RulesDraftError, match="No cached comments"):
        draft_rules(
            repo_name="myrepo",
            mining_db=db,
            repo_manager=FakeRepoManager(repo_path),
            guides_dir=tmp_path / "guides",
            run_agent_fn=lambda **kw: "unused",
        )


def test_draft_rules_writes_draft_file(tmp_path: Path):
    db = _seed_db(tmp_path)
    repo_path = tmp_path / "checkout"
    repo_path.mkdir()
    guides_dir = tmp_path / "guides"

    captured = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "# Draft Review Rules: myrepo\n\nrule one"

    out_path = draft_rules(
        repo_name="myrepo",
        mining_db=db,
        repo_manager=FakeRepoManager(repo_path),
        guides_dir=guides_dir,
        run_agent_fn=fake_run_agent,
    )

    assert out_path == guides_dir / "myrepo" / "draft-rules.md"
    assert out_path.read_text() == "# Draft Review Rules: myrepo\n\nrule one"
    # The mined-comments artifact is cleaned up after the agent runs.
    assert not (repo_path / ".bb_review_mined_comments.md").exists()
    # The agent prompt mentions the repo and the comment text.
    assert "myrepo" in captured["prompt"]


def test_draft_rules_includes_existing_patterns(tmp_path: Path):
    db = _seed_db(tmp_path)
    repo_path = tmp_path / "checkout"
    repo_path.mkdir()
    guides_dir = tmp_path / "guides"
    tp_dir = guides_dir / "myrepo"
    tp_dir.mkdir(parents=True)
    (tp_dir / "technical-patterns.md").write_text("ALREADY DOCUMENTED RULE")

    captured = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "# Draft Review Rules: myrepo\n\nnew rule"

    draft_rules(
        repo_name="myrepo",
        mining_db=db,
        repo_manager=FakeRepoManager(repo_path),
        guides_dir=guides_dir,
        run_agent_fn=fake_run_agent,
    )
    assert "ALREADY DOCUMENTED RULE" in captured["prompt"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rules_synthesizer.py::test_draft_rules_writes_draft_file -v`
Expected: FAIL with `ImportError: cannot import name 'RulesDraftError'` / `'draft_rules'`

- [ ] **Step 4: Write minimal implementation**

In `bb_review/rules/synthesizer.py`, update the import block at the top and add the new code.

Change the top of the file from:
```python
"""Synthesize cached reviewer comments into a draft rules document."""

from ..db.mining_db import MinedComment
```
to:
```python
"""Synthesize cached reviewer comments into a draft rules document."""

from collections.abc import Callable
from pathlib import Path

from ..db.mining_db import MinedComment, MiningDatabase
from .agent_runner import run_agent


class RulesDraftError(Exception):
    """Error drafting a rules document."""
```

Then append to the end of `bb_review/rules/synthesizer.py`:
```python
ARTIFACT_FILENAME = ".bb_review_mined_comments.md"


def draft_rules(
    repo_name: str,
    mining_db: MiningDatabase,
    repo_manager,
    guides_dir: Path,
    method: str = "claude",
    model: str | None = None,
    timeout: int = 600,
    binary_path: str | None = None,
    transcript_path: Path | None = None,
    run_agent_fn: Callable[..., str] = run_agent,
) -> Path:
    """Draft a rules file for a repository from its cached reviewer comments.

    Loads cached comments, checks out the repo, writes a comments artifact
    into the checkout, runs an agent, and writes the result to
    `guides/{repo_name}/draft-rules.md`.

    Args:
        repo_name: Config repository name (also the cache `repository` key).
        mining_db: Cache database holding the fetched comments.
        repo_manager: RepoManager used to clone/checkout the repo.
        guides_dir: Path to the `guides/` directory.
        method: Agent backend, 'claude' or 'codex'.
        model: Optional model override for the agent.
        timeout: Agent timeout in seconds.
        binary_path: Optional explicit agent binary path.
        transcript_path: If set, the agent transcript is saved here.
        run_agent_fn: Agent runner callable (overridable for tests).

    Returns:
        Path to the written draft-rules.md file.

    Raises:
        RulesDraftError: If no comments are cached or the agent yields nothing.
    """
    comments = mining_db.get_comments_for_repo(repo_name)
    if not comments:
        raise RulesDraftError(
            f"No cached comments for '{repo_name}'. "
            f"Run 'bb-review rules fetch {repo_name}' first."
        )

    repo_manager.ensure_clone(repo_name)
    repo_config = repo_manager.get_repo(repo_name)
    repo_manager.checkout(repo_name, repo_config.default_branch)
    repo_path = repo_manager.get_local_path(repo_name)

    existing_path = guides_dir / repo_name / "technical-patterns.md"
    existing_patterns = existing_path.read_text() if existing_path.exists() else None

    artifact = format_comments_artifact(comments)
    artifact_path = repo_path / ARTIFACT_FILENAME
    artifact_path.write_text(artifact)

    prompt = build_rules_prompt(repo_name, artifact, existing_patterns)

    try:
        output = run_agent_fn(
            method=method,
            repo_path=repo_path,
            prompt=prompt,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            transcript_path=transcript_path,
        )
    finally:
        artifact_path.unlink(missing_ok=True)

    if not output.strip():
        raise RulesDraftError("Agent produced empty output")

    out_dir = guides_dir / repo_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "draft-rules.md"
    out_path.write_text(output)
    return out_path
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rules_synthesizer.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 6: Commit**

```bash
git add bb_review/rules/synthesizer.py tests/unit/test_rules_synthesizer.py
git commit -m "feat: add draft_rules orchestration for rules synthesis"
```

---

## Task 9: rules CLI command group

**Files:**
- Create: `bb_review/cli/rules.py`
- Modify: `bb_review/cli/__init__.py` (register the `rules` submodule)
- Test: `tests/cli/test_rules.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_rules.py`:
```python
"""Tests for the rules CLI commands."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from bb_review.cli import main
from bb_review.db.mining_db import MiningDatabase
from bb_review.triage.models import RBComment


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    content = f"""
reviewboard:
  url: "https://rb.example.com"
  api_token: "test-token"
  bot_username: "ai-reviewer"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "test-key"
database:
  path: "{tmp_path / "state.db"}"
repositories:
  - name: testrepo
    rb_repo_name: test-repo
    local_path: "{tmp_path / "testrepo"}"
    remote_url: "https://git.example.com/testrepo.git"
"""
    path = tmp_path / "config.yaml"
    path.write_text(content)
    return path


def _mining_db(tmp_path: Path) -> MiningDatabase:
    return MiningDatabase(tmp_path / "rules_mining.db")


def test_rules_show_empty(runner: CliRunner, config_path: Path):
    result = runner.invoke(main, ["--config", str(config_path), "rules", "show", "testrepo"])
    assert result.exit_code == 0
    assert "Review requests: 0" in result.output


def test_rules_show_with_data(runner: CliRunner, config_path: Path, tmp_path: Path):
    db = _mining_db(tmp_path)
    db.record_review_request(
        rr_id=1,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="s",
        submitter="bob",
        branch="main",
        rb_last_updated="d",
        comments=[RBComment(review_id=2, comment_id=3, reviewer="a", text="t")],
    )
    result = runner.invoke(main, ["--config", str(config_path), "rules", "show", "testrepo"])
    assert result.exit_code == 0
    assert "Review requests: 1" in result.output
    assert "Comments:        1" in result.output


def test_rules_fetch_reports_counts(runner: CliRunner, config_path: Path, monkeypatch):
    class StubRBClient:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            return None

    monkeypatch.setattr("bb_review.rr.rb_client.ReviewBoardClient", StubRBClient)
    monkeypatch.setattr(
        "bb_review.cli.rules.fetch_repo_rules_data",
        lambda **kw: {"total": 3, "fetched": 2, "skipped": 1, "comments": 7},
    )

    result = runner.invoke(
        main, ["--config", str(config_path), "rules", "fetch", "testrepo"]
    )
    assert result.exit_code == 0
    assert "3 RRs found" in result.output
    assert "2 fetched" in result.output
    assert "7 comments cached" in result.output


def test_rules_fetch_unknown_repo(runner: CliRunner, config_path: Path):
    result = runner.invoke(
        main, ["--config", str(config_path), "rules", "fetch", "nope"]
    )
    assert result.exit_code == 1
    assert "Error" in result.output


def test_rules_draft_writes_file(runner: CliRunner, config_path: Path, monkeypatch, tmp_path: Path):
    out_file = tmp_path / "draft-rules.md"
    monkeypatch.setattr(
        "bb_review.cli.rules.draft_rules",
        lambda **kw: out_file,
    )
    result = runner.invoke(
        main, ["--config", str(config_path), "rules", "draft", "testrepo"]
    )
    assert result.exit_code == 0
    assert f"Wrote {out_file}" in result.output


def test_rules_draft_handles_missing_cache(runner: CliRunner, config_path: Path, monkeypatch):
    from bb_review.rules.synthesizer import RulesDraftError

    def _raise(**kw):
        raise RulesDraftError("No cached comments for 'testrepo'.")

    monkeypatch.setattr("bb_review.cli.rules.draft_rules", _raise)
    result = runner.invoke(
        main, ["--config", str(config_path), "rules", "draft", "testrepo"]
    )
    assert result.exit_code == 1
    assert "No cached comments" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_rules.py -v`
Expected: FAIL — `rules` is not a registered command (`No such command 'rules'`).

- [ ] **Step 3: Create the CLI module**

Create `bb_review/cli/rules.py`:
```python
"""Rules-mining commands: cache reviewer comments and draft repo rules."""

import logging
from pathlib import Path
import sys

import click

from ..config import Config
from ..db.mining_db import MiningDatabase
from ..git import RepoManager, RepoManagerError
from ..rules.fetcher import fetch_repo_rules_data
from ..rules.synthesizer import RulesDraftError, draft_rules
from . import get_config, main


logger = logging.getLogger(__name__)


def _mining_db_path(config: Config) -> Path:
    """Cache DB sits next to the state DB, e.g. ~/.bb_review/rules_mining.db."""
    return config.database.resolved_path.parent / "rules_mining.db"


def _guides_dir() -> Path:
    """Path to the repo's guides/ directory."""
    return Path(__file__).parent.parent.parent / "guides"


@main.group()
def rules() -> None:
    """Mine reviewer comments and draft repo review rules."""


@rules.command("fetch")
@click.argument("repo_name")
@click.option("--count", default=30, help="Max recent review requests to mine.")
@click.option(
    "--days", default=0, help="Only mine RRs updated within N days (0 = no limit)."
)
@click.option("--refresh", is_flag=True, help="Re-fetch RRs even if already cached.")
@click.pass_context
def rules_fetch(
    ctx: click.Context, repo_name: str, count: int, days: int, refresh: bool
) -> None:
    """Fetch reviewer comments for REPO_NAME into the mining cache."""
    config = get_config(ctx)
    repo_manager = RepoManager(config.get_all_repos())
    try:
        repo_config = repo_manager.get_repo(repo_name)
    except RepoManagerError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    from ..rr.rb_client import ReviewBoardClient

    rb_client = ReviewBoardClient(
        url=config.reviewboard.url,
        bot_username=config.reviewboard.bot_username,
        api_token=config.reviewboard.api_token,
        username=config.reviewboard.username,
        password=config.reviewboard.get_password(),
        use_kerberos=config.reviewboard.use_kerberos,
    )
    rb_client.connect()

    mining_db = MiningDatabase(_mining_db_path(config))

    def _progress(current: int, total: int, n_comments: int) -> None:
        click.echo(
            f"\r  [{current}/{total}] processed (+{n_comments} comments)", nl=False
        )

    click.echo(f"Fetching reviewer comments for '{repo_name}' (last {count} RRs)...")
    counts = fetch_repo_rules_data(
        rb_client=rb_client,
        mining_db=mining_db,
        repo_name=repo_name,
        rb_repo_name=repo_config.rb_repo_name,
        bot_username=config.reviewboard.bot_username,
        count=count,
        days=days,
        refresh=refresh,
        on_progress=_progress,
    )
    click.echo()
    click.echo(
        f"Done: {counts['total']} RRs found, "
        f"{counts['fetched']} fetched, "
        f"{counts['skipped']} skipped, "
        f"{counts['comments']} comments cached."
    )


@rules.command("draft")
@click.argument("repo_name")
@click.option(
    "--method",
    type=click.Choice(["claude", "codex"]),
    default="claude",
    help="Agent backend for synthesis.",
)
@click.option(
    "--transcript",
    type=click.Path(path_type=Path),
    help="Save the agent transcript to this path.",
)
@click.pass_context
def rules_draft(
    ctx: click.Context, repo_name: str, method: str, transcript: Path | None
) -> None:
    """Draft guides/REPO_NAME/draft-rules.md from cached comments."""
    config = get_config(ctx)
    repo_manager = RepoManager(config.get_all_repos())
    mining_db = MiningDatabase(_mining_db_path(config))

    click.echo(f"Drafting rules for '{repo_name}' via {method}...")
    try:
        out_path = draft_rules(
            repo_name=repo_name,
            mining_db=mining_db,
            repo_manager=repo_manager,
            guides_dir=_guides_dir(),
            method=method,
            transcript_path=transcript,
        )
    except (RulesDraftError, RepoManagerError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"Wrote {out_path}")


@rules.command("show")
@click.argument("repo_name")
@click.pass_context
def rules_show(ctx: click.Context, repo_name: str) -> None:
    """Show what is cached for REPO_NAME."""
    config = get_config(ctx)
    mining_db = MiningDatabase(_mining_db_path(config))
    stats = mining_db.get_repo_stats(repo_name)
    click.echo(f"Cached for '{repo_name}':")
    click.echo(f"  Review requests: {stats.review_request_count}")
    click.echo(f"  Comments:        {stats.comment_count}")
```

- [ ] **Step 4: Register the submodule**

In `bb_review/cli/__init__.py`, find the import block:
```python
from . import (
    analyze,  # noqa: E402, F401
    claude_code,  # noqa: E402, F401
    cocoindex,  # noqa: E402, F401
    codex,  # noqa: E402, F401
    comments,  # noqa: E402, F401
    db,  # noqa: E402, F401
    interactive,  # noqa: E402, F401
    opencode,  # noqa: E402, F401
    poll,  # noqa: E402, F401
    queue,  # noqa: E402, F401
    repos,  # noqa: E402, F401
    resolve,  # noqa: E402, F401
    submit,  # noqa: E402, F401
    transcript,  # noqa: E402, F401
    triage,  # noqa: E402, F401
    utils,  # noqa: E402, F401
)
```

Add `rules` after `resolve`:
```python
from . import (
    analyze,  # noqa: E402, F401
    claude_code,  # noqa: E402, F401
    cocoindex,  # noqa: E402, F401
    codex,  # noqa: E402, F401
    comments,  # noqa: E402, F401
    db,  # noqa: E402, F401
    interactive,  # noqa: E402, F401
    opencode,  # noqa: E402, F401
    poll,  # noqa: E402, F401
    queue,  # noqa: E402, F401
    repos,  # noqa: E402, F401
    resolve,  # noqa: E402, F401
    rules,  # noqa: E402, F401
    submit,  # noqa: E402, F401
    transcript,  # noqa: E402, F401
    triage,  # noqa: E402, F401
    utils,  # noqa: E402, F401
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_rules.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add bb_review/cli/rules.py bb_review/cli/__init__.py tests/cli/test_rules.py
git commit -m "feat: add rules CLI command group (fetch, draft, show)"
```

---

## Task 10: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit and CLI test suites**

Run: `uv run pytest tests/unit/ tests/cli/ -v`
Expected: PASS — all tests, including the new `test_mining_db.py`, `test_rb_client.py`, `test_rules_fetcher.py`, `test_rules_synthesizer.py`, `test_agent_runner.py`, `test_rules.py`, and no regressions in existing tests.

- [ ] **Step 2: Run the linter**

Run: `task check`
Expected: `All checks passed!` for both `ruff check` and `ruff format`. If formatting differs, run `task format` and re-run `task check`, then amend nothing — create a follow-up commit if files changed.

- [ ] **Step 3: Smoke-test the CLI wiring**

Run: `uv run bb-review rules --help`
Expected: Help text listing the `fetch`, `draft`, and `show` subcommands.

Run: `uv run bb-review rules fetch --help`
Expected: Help text showing `--count`, `--days`, and `--refresh` options.

- [ ] **Step 4: Commit any formatting fixes**

If `task format` changed files in Step 2:
```bash
git add -u
git commit -m "chore: apply ruff formatting to rules-mining modules"
```

If nothing changed, skip this step.

---

## Self-Review Notes

Spec coverage check against `2026-05-19-rules-mining-command-design.md`:

- Command surface (`rules fetch` / `draft` / `show`) — Task 9.
- Cache DB `rules_mining.db` with `mined_review_requests` + `mined_comments` — Tasks 1-3; path resolved in Task 9 (`_mining_db_path`).
- `issue_status` preserved through the pipeline — schema (Task 1), `MinedComment` (Task 1), artifact tagging (Task 6).
- Fetch: submitted + discarded merge, lightweight RR listing, incremental skip, per-RR error tolerance — Tasks 4 and 5.
- Synthesis: comments artifact, repo checkout, agent run, existing-`technical-patterns.md` context, output to `draft-rules.md`, `--transcript` — Tasks 6-9.
- `--method` defaults to `claude` — Task 9 (`click.Choice`, `default="claude"`).
- `--refresh` is explicit-only — Task 5 (`refresh` flag gates the `has_review_request` skip).
- Error handling: zero-comment draft, agent empty output — Task 8 (`RulesDraftError`); RB auth failure handled by the existing `MainGroup.invoke` catch.
- Testing: `MockRBClient` extension, `MiningDatabase` unit tests, CLI tests — Tasks 1-3, 5, 9.
