# Rules-Mining Diff-Hunk Caching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `--with-diff-hunks` flag to `bb-review rules fetch` that caches the unified diff hunk for each diff comment alongside the comment, and backfills missing hunks on already-cached RRs.

**Architecture:** Two additive columns on `mined_comments` (`diff_revision`, `diff_hunk`), two extra fields on `RBComment`/`MinedComment`, and an opt-in augmentation in `bb_review/rules/fetcher.py` that fetches each RR's raw diff at most once (memoized), slices the hunk via the existing `extract_diff_hunk()` helper, and either inserts (new RR) or `UPDATE`s (backfill on a cached RR). The synthesis artifact renders hunks as fenced diff blocks when present.

**Tech Stack:** Python 3.10+, Click, SQLite (`sqlite3` stdlib), pytest, `uv` for tooling. Reuses `bb_review/reviewers/diff_utils.py::extract_diff_hunk`, `ReviewBoardClient.get_diff`, and the existing `RBCommentFetcher`.

Reference spec: `docs/superpowers/specs/2026-05-20-rules-fetch-diff-hunk-design.md`.

---

## File Structure

**Modify:**
- `bb_review/triage/models.py` — add `diff_revision` and `diff_hunk` fields to `RBComment`.
- `bb_review/db/mining_db.py` — schema migration (two `ALTER TABLE`s), `MinedComment` new fields, INSERT/SELECT include the new columns, two new methods (`get_comments_missing_hunks`, `update_comment_diff_hunk`).
- `bb_review/rr/rb_fetcher.py` — populate `RBComment.diff_revision` from the filediff href.
- `bb_review/rules/fetcher.py` — new `with_diff_hunks` parameter, memoized diff fetch, hunk extraction for newly-fetched comments **and** backfill for already-cached RRs. Adds a `hunks_backfilled` counter.
- `bb_review/rules/synthesizer.py` — `format_comments_artifact` renders a fenced diff block under each comment that has a `diff_hunk`; one extra sentence in `build_rules_prompt`.
- `bb_review/cli/rules.py` — add the `--with-diff-hunks` Click option to `rules fetch` and pass through.
- `tests/mocks/rb_client.py` — extend `MockRBClient.get_diff` to support `(rr_id, revision)` keying.

**Test:**
- `tests/unit/test_mining_db.py` (extend)
- `tests/unit/test_rb_fetcher.py` (extend)
- `tests/unit/test_rules_fetcher.py` (extend)
- `tests/unit/test_rules_synthesizer.py` (extend)
- `tests/cli/test_rules.py` (extend)

---

## Task 1: Extend dataclasses, schema, and round-trip

**Files:**
- Modify: `bb_review/triage/models.py` (add fields to `RBComment`)
- Modify: `bb_review/db/mining_db.py` (`MinedComment` fields, schema migration, INSERT, SELECT)
- Test: `tests/unit/test_mining_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mining_db.py`:
```python
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
        "INSERT INTO mined_comments (rr_id, review_id, comment_id, text) "
        "VALUES (1, 2, 3, 'existing comment')"
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_mining_db.py::test_diff_hunk_round_trips_through_record_and_read tests/unit/test_mining_db.py::test_schema_migration_adds_diff_columns -v`
Expected: FAIL — `RBComment` rejects the `diff_revision` / `diff_hunk` kwargs (TypeError) for the first test, and the second test fails on the column check.

- [ ] **Step 3: Add the new fields to `RBComment`**

In `bb_review/triage/models.py`, find the `RBComment` dataclass:
```python
@dataclass
class RBComment:
    """A single comment fetched from Review Board."""

    review_id: int
    comment_id: int
    reviewer: str
    text: str
    file_path: str | None = None
    line_number: int | None = None
    issue_opened: bool = False
    issue_status: str | None = None
    reply_to_id: int | None = None
    is_body_comment: bool = False
```

Replace it with:
```python
@dataclass
class RBComment:
    """A single comment fetched from Review Board."""

    review_id: int
    comment_id: int
    reviewer: str
    text: str
    file_path: str | None = None
    line_number: int | None = None
    issue_opened: bool = False
    issue_status: str | None = None
    reply_to_id: int | None = None
    is_body_comment: bool = False
    diff_revision: int | None = None
    diff_hunk: str | None = None
```

- [ ] **Step 4: Add the new fields to `MinedComment`**

In `bb_review/db/mining_db.py`, find the `MinedComment` dataclass:
```python
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
```

Replace it with:
```python
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
```

- [ ] **Step 5: Migrate the schema in `_ensure_db`**

In `bb_review/db/mining_db.py`, find the `_ensure_db` method. It currently ends with the closing `"""` of the `executescript` argument. Add the migration block right after the `executescript` call so the method body looks like:

```python
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
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(mined_comments)").fetchall()
            }
            if "diff_revision" not in cols:
                conn.execute("ALTER TABLE mined_comments ADD COLUMN diff_revision INTEGER")
            if "diff_hunk" not in cols:
                conn.execute("ALTER TABLE mined_comments ADD COLUMN diff_hunk TEXT")
```

Note both the `CREATE TABLE` definition (for fresh DBs) and the `ALTER TABLE`s (for upgrades) need the new columns. Fresh DBs hit the `CREATE` and the `ALTER` is a no-op (column already present).

- [ ] **Step 6: Update `record_review_request` to persist the new fields**

In `bb_review/db/mining_db.py`, find the INSERT loop inside `record_review_request`:

```python
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

Replace with:
```python
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
```

- [ ] **Step 7: Update `get_comments_for_repo` to read the new fields**

In `bb_review/db/mining_db.py`, find the SELECT in `get_comments_for_repo`:

```python
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
```

Replace with:
```python
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
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mining_db.py -v`
Expected: PASS (all existing tests plus the two new ones).

- [ ] **Step 9: Commit**

```bash
git add bb_review/triage/models.py bb_review/db/mining_db.py tests/unit/test_mining_db.py
git commit -m "feat: persist diff_revision and diff_hunk on cached comments"
```

---

## Task 2: MiningDatabase backfill methods

**Files:**
- Modify: `bb_review/db/mining_db.py` (add two methods to `MiningDatabase`)
- Test: `tests/unit/test_mining_db.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mining_db.py`:
```python
def test_get_comments_missing_hunks_returns_only_eligible_rows(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=10,
        repository="r",
        rr_status="submitted",
        rr_summary="s",
        submitter="b",
        branch="main",
        rb_last_updated="d",
        comments=[
            # Diff comment, hunk already present -- excluded.
            RBComment(
                review_id=1,
                comment_id=100,
                reviewer="a",
                text="t",
                file_path="src/a.c",
                line_number=5,
                diff_revision=2,
                diff_hunk="@@ existing",
            ),
            # Diff comment, hunk missing -- included.
            RBComment(
                review_id=1,
                comment_id=101,
                reviewer="a",
                text="t",
                file_path="src/b.c",
                line_number=8,
                diff_revision=2,
            ),
            # Body comment (no file) -- excluded.
            RBComment(
                review_id=1,
                comment_id=102,
                reviewer="a",
                text="t",
                is_body_comment=True,
            ),
        ],
    )
    missing = db.get_comments_missing_hunks(10)
    assert [c.comment_id for c in missing] == [101]
    assert missing[0].diff_revision == 2


def test_update_comment_diff_hunk_sets_value(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=10,
        repository="r",
        rr_status="submitted",
        rr_summary="s",
        submitter="b",
        branch="main",
        rb_last_updated="d",
        comments=[
            RBComment(
                review_id=1,
                comment_id=101,
                reviewer="a",
                text="t",
                file_path="src/b.c",
                line_number=8,
                diff_revision=2,
            ),
        ],
    )
    db.update_comment_diff_hunk(rr_id=10, comment_id=101, hunk="@@ filled")
    comments = db.get_comments_for_repo("r")
    assert comments[0].diff_hunk == "@@ filled"


def test_update_comment_diff_hunk_ignores_body_comments(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=10,
        repository="r",
        rr_status="submitted",
        rr_summary="s",
        submitter="b",
        branch="main",
        rb_last_updated="d",
        comments=[
            RBComment(
                review_id=1, comment_id=200, reviewer="a", text="t", is_body_comment=True
            ),
        ],
    )
    # WHERE file_path IS NOT NULL guards against accidental body-comment writes.
    db.update_comment_diff_hunk(rr_id=10, comment_id=200, hunk="@@ should not apply")
    comments = db.get_comments_for_repo("r")
    assert comments[0].diff_hunk is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_mining_db.py::test_get_comments_missing_hunks_returns_only_eligible_rows tests/unit/test_mining_db.py::test_update_comment_diff_hunk_sets_value tests/unit/test_mining_db.py::test_update_comment_diff_hunk_ignores_body_comments -v`
Expected: FAIL — `MiningDatabase` has no `get_comments_missing_hunks` / `update_comment_diff_hunk`.

- [ ] **Step 3: Add the two methods**

In `bb_review/db/mining_db.py`, add these methods at the end of the `MiningDatabase` class (just after `get_repo_stats`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mining_db.py -v`
Expected: PASS (all existing + three new).

- [ ] **Step 5: Commit**

```bash
git add bb_review/db/mining_db.py tests/unit/test_mining_db.py
git commit -m "feat: add backfill query+update for cached diff hunks"
```

---

## Task 3: Populate diff_revision in RBCommentFetcher

**Files:**
- Modify: `bb_review/rr/rb_fetcher.py` (set `diff_revision` on each diff comment)
- Test: `tests/unit/test_rb_fetcher.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_rb_fetcher.py` (it already imports `RBCommentFetcher` and constructs mock review/comment dicts — follow the existing patterns there):

```python
def test_fetcher_sets_diff_revision_from_filediff_href():
    """RBCommentFetcher should populate RBComment.diff_revision from the
    /diffs/{rev}/ segment of the filediff href."""
    from bb_review.rr.rb_fetcher import RBCommentFetcher

    class StubClient:
        bot_username = "bot"
        _filediff_cache: dict = {7: [{"id": 99, "dest_file": "src/a.c"}]}

        def _warm_filediff_cache(self, rr_id):
            return None

        def get_reviews(self, rr_id):
            return [
                {
                    "id": 1,
                    "body_top": "",
                    "links": {"user": {"href": "/api/users/alice/"}},
                }
            ]

        def get_review_diff_comments(self, rr_id, review_id):
            return [
                {
                    "id": 555,
                    "text": "fix",
                    "first_line": 12,
                    "issue_opened": True,
                    "issue_status": "open",
                    "links": {
                        "filediff": {
                            "href": "/api/review-requests/7/diffs/3/files/99/"
                        }
                    },
                }
            ]

        def get_review_replies(self, rr_id, review_id):
            return []

    fetcher = RBCommentFetcher(StubClient(), bot_username="bot")
    comments = fetcher.fetch_all_comments(7)

    diff_comments = [c for c in comments if not c.is_body_comment]
    assert len(diff_comments) == 1
    assert diff_comments[0].diff_revision == 3
    assert diff_comments[0].file_path == "src/a.c"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rb_fetcher.py::test_fetcher_sets_diff_revision_from_filediff_href -v`
Expected: FAIL — `RBComment.diff_revision` is set to `None` because `RBCommentFetcher` does not populate it.

- [ ] **Step 3: Add a `_resolve_diff_revision` helper and populate the field**

In `bb_review/rr/rb_fetcher.py`, find `_resolve_file_path` (the last method in the class). Add a new private helper alongside it, and use it in `fetch_all_comments` where diff comments are appended.

First, add this helper as a new method on `RBCommentFetcher`:
```python
    def _resolve_diff_revision(self, diff_comment: dict) -> int | None:
        """Extract the diff revision from a diff comment's filediff href."""
        links = diff_comment.get("links", {})
        href = links.get("filediff", {}).get("href", "")
        match = re.search(r"/diffs/(\d+)/", href)
        if match:
            return int(match.group(1))
        return None
```

Then in the diff-comment loop inside `fetch_all_comments`, find:
```python
            diff_comments = self.rb_client.get_review_diff_comments(rr_id, review_id)
            for dc in diff_comments:
                file_path = self._resolve_file_path(rr_id, dc)
                comments.append(
                    RBComment(
                        review_id=review_id,
                        comment_id=dc["id"],
                        reviewer=reviewer,
                        text=dc.get("text", ""),
                        file_path=file_path,
                        line_number=dc.get("first_line"),
                        issue_opened=dc.get("issue_opened", False),
                        issue_status=dc.get("issue_status"),
                    )
                )
```

Replace with:
```python
            diff_comments = self.rb_client.get_review_diff_comments(rr_id, review_id)
            for dc in diff_comments:
                file_path = self._resolve_file_path(rr_id, dc)
                diff_revision = self._resolve_diff_revision(dc)
                comments.append(
                    RBComment(
                        review_id=review_id,
                        comment_id=dc["id"],
                        reviewer=reviewer,
                        text=dc.get("text", ""),
                        file_path=file_path,
                        line_number=dc.get("first_line"),
                        issue_opened=dc.get("issue_opened", False),
                        issue_status=dc.get("issue_status"),
                        diff_revision=diff_revision,
                    )
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rb_fetcher.py -v`
Expected: PASS (existing tests + new one).

- [ ] **Step 5: Commit**

```bash
git add bb_review/rr/rb_fetcher.py tests/unit/test_rb_fetcher.py
git commit -m "feat: populate diff_revision on diff comments in RBCommentFetcher"
```

---

## Task 4: Extend MockRBClient.get_diff to key by (rr_id, revision)

**Files:**
- Modify: `tests/mocks/rb_client.py` (extend `MockRBClient.get_diff`)

This is test-support code with no separate test; later tasks exercise it.

- [ ] **Step 1: Update the mock's `diffs` storage and `get_diff` method**

In `tests/mocks/rb_client.py`, find `MockRBClient.__init__`:
```python
    def __init__(
        self,
        reviews: dict[int, dict] | None = None,
        diffs: dict[int, MockDiffInfo] | None = None,
        ...
```

`diffs: dict[int, MockDiffInfo]` (keyed by `rr_id` only) is what exists today. Keep it for backward compatibility AND accept a `diffs_by_rev: dict[tuple[int, int], MockDiffInfo] | None = None` for precise per-revision lookup.

Change the `__init__` signature so that after `diffs: dict[int, MockDiffInfo] | None = None` you add:
```python
        diffs_by_rev: dict[tuple[int, int], MockDiffInfo] | None = None,
```

And in the body, alongside `self.diffs = diffs or {}`, add:
```python
        self.diffs_by_rev = diffs_by_rev or {}
```

Now find `get_diff`:
```python
    def get_diff(self, review_request_id: int, diff_revision: int | None = None) -> MockDiffInfo:
        """Get mock diff info.

        Args:
            review_request_id: Review ID.
            diff_revision: Optional specific revision.

        Returns:
            MockDiffInfo instance.
        """
        if review_request_id in self.diffs:
            return self.diffs[review_request_id]

        return MockDiffInfo(
            diff_revision=diff_revision or 1,
            base_commit_id="abc123def456",
            raw_diff=SAMPLE_DIFF,
        )
```

Replace with:
```python
    def get_diff(self, review_request_id: int, diff_revision: int | None = None) -> MockDiffInfo:
        """Get mock diff info.

        Precedence: `diffs_by_rev[(rr_id, rev)]` > `diffs[rr_id]` > default.

        Args:
            review_request_id: Review ID.
            diff_revision: Optional specific revision.

        Returns:
            MockDiffInfo instance.
        """
        if diff_revision is not None:
            keyed = self.diffs_by_rev.get((review_request_id, diff_revision))
            if keyed is not None:
                return keyed
        if review_request_id in self.diffs:
            return self.diffs[review_request_id]

        return MockDiffInfo(
            diff_revision=diff_revision or 1,
            base_commit_id="abc123def456",
            raw_diff=SAMPLE_DIFF,
        )
```

- [ ] **Step 2: Verify existing tests still pass**

Run: `uv run pytest tests/ -q`
Expected: PASS — backward-compatible change.

- [ ] **Step 3: Commit**

```bash
git add tests/mocks/rb_client.py
git commit -m "test: let MockRBClient.get_diff key by (rr_id, revision)"
```

---

## Task 5: Diff-hunk fetch for newly-fetched RRs

**Files:**
- Modify: `bb_review/rules/fetcher.py` (add `with_diff_hunks` path for new RRs; memoization)
- Test: `tests/unit/test_rules_fetcher.py`

- [ ] **Step 1: Update the imports in the test file**

In `tests/unit/test_rules_fetcher.py`, find the import block at the top. It currently looks roughly like:
```python
from pathlib import Path

from bb_review.db.mining_db import MiningDatabase
from bb_review.rules.fetcher import fetch_repo_rules_data
from bb_review.triage.models import RBComment
from tests.mocks import MockRBClient
```

Replace with:
```python
from pathlib import Path

from bb_review.db.mining_db import MiningDatabase
from bb_review.rules.fetcher import fetch_repo_rules_data
from bb_review.triage.models import RBComment
from tests.mocks import MockDiffInfo, MockRBClient
```

- [ ] **Step 2: Append the failing tests**

Append to `tests/unit/test_rules_fetcher.py`:
```python
class _RecordingCommentFetcher:
    """Comment fetcher returning canned comments and counting calls."""

    def __init__(self, comments_by_rr: dict[int, list[RBComment]]):
        self.comments_by_rr = comments_by_rr
        self.calls: list[int] = []

    def fetch_all_comments(self, rr_id: int) -> list[RBComment]:
        self.calls.append(rr_id)
        return self.comments_by_rr.get(rr_id, [])


def _diff_for_line(file_path: str, line: int) -> str:
    """Build a tiny unified diff whose new-file hunk covers `line`."""
    start = max(1, line - 1)
    return (
        f"diff --git a/{file_path} b/{file_path}\n"
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        f"@@ -{start},2 +{start},3 @@\n"
        " context_before\n"
        "+added_line\n"
        " context_after\n"
    )


def test_fetch_with_diff_hunks_populates_hunk_for_diff_comments(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    rr = {
        "id": 1,
        "summary": "x",
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "branch": "main",
        "links": {"submitter": {"title": "alice"}},
    }
    raw_diff = _diff_for_line("src/a.c", 2)
    rb = MockRBClient(
        repo_review_requests=[rr],
        diffs_by_rev={(1, 3): MockDiffInfo(diff_revision=3, raw_diff=raw_diff)},
    )
    fetcher = _RecordingCommentFetcher(
        {
            1: [
                RBComment(
                    review_id=10,
                    comment_id=20,
                    reviewer="bob",
                    text="check",
                    file_path="src/a.c",
                    line_number=2,
                    diff_revision=3,
                ),
                RBComment(
                    review_id=10,
                    comment_id=21,
                    reviewer="bob",
                    text="general",
                    is_body_comment=True,
                ),
            ]
        }
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=True,
        comment_fetcher=fetcher,
    )
    assert counts["fetched"] == 1
    assert counts["hunks_backfilled"] == 0

    saved = db.get_comments_for_repo("r")
    by_id = {c.comment_id: c for c in saved}
    assert "@@ -1,2 +1,3 @@" in (by_id[20].diff_hunk or "")
    assert by_id[21].diff_hunk is None  # body comment, untouched


def test_fetch_memoizes_diff_per_rr_revision(tmp_path: Path, monkeypatch):
    """Multiple comments on the same (rr_id, rev) must trigger get_diff once."""
    db = MiningDatabase(tmp_path / "m.db")
    rr = {
        "id": 1,
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "links": {"submitter": {"title": "alice"}},
    }
    raw_diff = _diff_for_line("src/a.c", 2)
    rb = MockRBClient(
        repo_review_requests=[rr],
        diffs_by_rev={(1, 3): MockDiffInfo(diff_revision=3, raw_diff=raw_diff)},
    )

    call_count = {"n": 0}
    original_get_diff = rb.get_diff

    def counting_get_diff(*args, **kw):
        call_count["n"] += 1
        return original_get_diff(*args, **kw)

    monkeypatch.setattr(rb, "get_diff", counting_get_diff)

    fetcher = _RecordingCommentFetcher(
        {
            1: [
                RBComment(
                    review_id=10,
                    comment_id=20 + i,
                    reviewer="bob",
                    text="t",
                    file_path="src/a.c",
                    line_number=2,
                    diff_revision=3,
                )
                for i in range(5)
            ]
        }
    )

    fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=True,
        comment_fetcher=fetcher,
    )
    assert call_count["n"] == 1  # five comments, one diff fetch


def test_fetch_with_diff_hunks_continues_when_get_diff_fails(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    rr = {
        "id": 1,
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "links": {"submitter": {"title": "alice"}},
    }
    rb = MockRBClient(repo_review_requests=[rr])

    def failing_get_diff(*args, **kw):
        raise RuntimeError("simulated diff failure")

    rb.get_diff = failing_get_diff
    fetcher = _RecordingCommentFetcher(
        {
            1: [
                RBComment(
                    review_id=10,
                    comment_id=20,
                    reviewer="bob",
                    text="t",
                    file_path="src/a.c",
                    line_number=2,
                    diff_revision=3,
                )
            ]
        }
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=True,
        comment_fetcher=fetcher,
    )
    # The RR was still recorded; only the hunk is missing.
    assert counts["fetched"] == 1
    saved = db.get_comments_for_repo("r")
    assert saved[0].diff_hunk is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rules_fetcher.py::test_fetch_with_diff_hunks_populates_hunk_for_diff_comments -v`
Expected: FAIL — `fetch_repo_rules_data` does not accept `with_diff_hunks`.

- [ ] **Step 4: Wire `with_diff_hunks` into the fetcher**

In `bb_review/rules/fetcher.py`, update the import block and the function. Change the top of the file from:
```python
"""Fetch reviewer comments from Review Board into the mining cache."""

from collections.abc import Callable
import logging

from ..db.mining_db import MiningDatabase
from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher
```

to:
```python
"""Fetch reviewer comments from Review Board into the mining cache."""

from collections.abc import Callable
import logging

from ..db.mining_db import MinedComment, MiningDatabase
from ..reviewers.diff_utils import extract_diff_hunk
from ..rr.rb_client import ReviewBoardClient
from ..rr.rb_fetcher import RBCommentFetcher
from ..triage.models import RBComment
```

(The `MinedComment` and `RBComment` imports are used by the new helpers added in this task and in Task 6; bring them in now.)

Then replace the `fetch_repo_rules_data` function entirely with this version, which adds the `with_diff_hunks` parameter, the diff memoization helper, the new-RR hunk augmentation, and the `hunks_backfilled` counter (set to 0 for now; Task 6 wires actual backfill):

```python
def fetch_repo_rules_data(
    rb_client: ReviewBoardClient,
    mining_db: MiningDatabase,
    repo_name: str,
    rb_repo_name: str,
    bot_username: str,
    count: int,
    days: int = 0,
    refresh: bool = False,
    with_diff_hunks: bool = False,
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
        with_diff_hunks: If True, also fetch and cache the diff hunk for each
            diff comment. For already-cached RRs this acts as a backfill.
        on_progress: Called with (current, total, comment_count) per RR.
        comment_fetcher: Override for the RBCommentFetcher (used in tests).

    Returns:
        Counts dict with keys: total, fetched, skipped, comments,
        hunks_backfilled.
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
    hunks_backfilled = 0

    diff_cache: dict[tuple[int, int], str | None] = {}

    def _get_raw_diff(rr_id: int, rev: int) -> str | None:
        """Memoized per-(rr_id, rev) raw-diff fetch.

        Returns None and caches the negative result if the RB call fails,
        so a single bad diff fetch doesn't abort the batch and doesn't get
        retried for every comment on that filediff.
        """
        key = (rr_id, rev)
        if key in diff_cache:
            return diff_cache[key]
        try:
            raw = rb_client.get_diff(rr_id, rev).raw_diff
        except Exception as e:
            logger.warning(f"Failed to fetch diff for RR #{rr_id} rev {rev}: {e}")
            diff_cache[key] = None
            return None
        diff_cache[key] = raw
        return raw

    def _augment_with_hunks(rr_id: int, comments: list[RBComment]) -> None:
        """Set comment.diff_hunk in place for diff comments with a known rev."""
        for c in comments:
            if c.is_body_comment or not c.file_path or not c.line_number:
                continue
            if c.diff_revision is None:
                continue
            raw = _get_raw_diff(rr_id, c.diff_revision)
            if raw is None:
                continue
            c.diff_hunk = extract_diff_hunk(raw, c.file_path, c.line_number)

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

        if with_diff_hunks:
            _augment_with_hunks(rr_id, comments)

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
        "hunks_backfilled": hunks_backfilled,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_rules_fetcher.py -v`
Expected: PASS (existing tests + three new).

- [ ] **Step 6: Commit**

```bash
git add bb_review/rules/fetcher.py tests/unit/test_rules_fetcher.py
git commit -m "feat: fetch diff hunks for newly-mined comments"
```

---

## Task 6: Backfill diff hunks on already-cached RRs

**Files:**
- Modify: `bb_review/rules/fetcher.py` (extend skip path with backfill behavior)
- Test: `tests/unit/test_rules_fetcher.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/unit/test_rules_fetcher.py`:
```python
def test_fetch_with_diff_hunks_backfills_cached_rr(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    # Cache an RR ahead of time with one diff comment and no hunk.
    db.record_review_request(
        rr_id=1,
        repository="r",
        rr_status="submitted",
        rr_summary="cached",
        submitter="x",
        branch="main",
        rb_last_updated="d",
        comments=[
            RBComment(
                review_id=10,
                comment_id=20,
                reviewer="bob",
                text="check",
                file_path="src/a.c",
                line_number=2,
                diff_revision=3,
            )
        ],
    )

    rr = {
        "id": 1,
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "links": {"submitter": {"title": "alice"}},
    }
    raw_diff = _diff_for_line("src/a.c", 2)
    rb = MockRBClient(
        repo_review_requests=[rr],
        diffs_by_rev={(1, 3): MockDiffInfo(diff_revision=3, raw_diff=raw_diff)},
    )
    # Will fail loudly if the backfill path ever calls into the comment fetcher.
    class _ExplodingFetcher:
        def fetch_all_comments(self, rr_id):
            raise AssertionError(
                "backfill must NOT re-fetch comments for an already-cached RR"
            )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=True,
        comment_fetcher=_ExplodingFetcher(),
    )
    assert counts["fetched"] == 0
    assert counts["skipped"] == 0  # neither fully skipped nor refetched
    assert counts["hunks_backfilled"] == 1

    saved = db.get_comments_for_repo("r")
    assert saved[0].diff_hunk is not None
    assert "@@ -1,2 +1,3 @@" in saved[0].diff_hunk


def test_fetch_without_diff_hunks_does_not_backfill_cached_rr(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=1,
        repository="r",
        rr_status="submitted",
        rr_summary="cached",
        submitter="x",
        branch="main",
        rb_last_updated="d",
        comments=[
            RBComment(
                review_id=10,
                comment_id=20,
                reviewer="bob",
                text="check",
                file_path="src/a.c",
                line_number=2,
                diff_revision=3,
            )
        ],
    )

    rr = {
        "id": 1,
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "links": {"submitter": {"title": "alice"}},
    }
    rb = MockRBClient(repo_review_requests=[rr])
    fetcher = _RecordingCommentFetcher({})

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=False,
        comment_fetcher=fetcher,
    )
    assert counts["skipped"] == 1
    assert counts["hunks_backfilled"] == 0
    saved = db.get_comments_for_repo("r")
    assert saved[0].diff_hunk is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rules_fetcher.py::test_fetch_with_diff_hunks_backfills_cached_rr -v`
Expected: FAIL — the cached-RR path always increments `skipped` and never backfills.

- [ ] **Step 3: Update the cached-RR skip branch to optionally backfill**

In `bb_review/rules/fetcher.py`, find the cached-RR branch inside the loop:
```python
        if not refresh and mining_db.has_review_request(rr_id):
            skipped += 1
            if on_progress:
                on_progress(i + 1, total, 0)
            continue
```

Replace with:
```python
        if not refresh and mining_db.has_review_request(rr_id):
            if with_diff_hunks:
                added = _backfill_hunks(rr_id, mining_db, _get_raw_diff)
                if added > 0:
                    hunks_backfilled += 1
                else:
                    skipped += 1
            else:
                skipped += 1
            if on_progress:
                on_progress(i + 1, total, 0)
            continue
```

Then add this module-level helper at the bottom of `bb_review/rules/fetcher.py`:
```python
def _backfill_hunks(
    rr_id: int,
    mining_db: MiningDatabase,
    get_raw_diff: Callable[[int, int], str | None],
) -> int:
    """Fill in diff_hunk for cached comments of `rr_id` that have it NULL.

    Returns the number of comments whose hunk was actually populated. A
    return of zero means either no missing hunks or no hunk could be
    extracted (e.g. line not in any hunk, or the diff fetch failed).
    """
    missing = mining_db.get_comments_missing_hunks(rr_id)
    filled = 0
    for c in missing:
        if c.diff_revision is None or c.file_path is None or c.line_number is None:
            continue
        raw = get_raw_diff(rr_id, c.diff_revision)
        if raw is None:
            continue
        hunk = extract_diff_hunk(raw, c.file_path, c.line_number)
        if hunk is None:
            continue
        mining_db.update_comment_diff_hunk(rr_id, c.comment_id, hunk)
        filled += 1
    return filled
```

- [ ] **Step 4: Run all fetcher tests to verify they pass**

Run: `uv run pytest tests/unit/test_rules_fetcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bb_review/rules/fetcher.py tests/unit/test_rules_fetcher.py
git commit -m "feat: backfill diff hunks on cached RRs when --with-diff-hunks is set"
```

---

## Task 7: Render diff hunks in the synthesis artifact

**Files:**
- Modify: `bb_review/rules/synthesizer.py` (artifact formatter + prompt sentence)
- Test: `tests/unit/test_rules_synthesizer.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/unit/test_rules_synthesizer.py`:
```python
def test_format_comments_artifact_renders_diff_hunk_when_present():
    artifact = format_comments_artifact(
        [
            _comment(
                file_path="src/a.c",
                line_number=2,
                diff_hunk="@@ -1,2 +1,3 @@\n context\n+added\n context",
            )
        ]
    )
    assert "```diff" in artifact
    assert "+added" in artifact
    assert "```" in artifact.split("```diff", 1)[1]  # closing fence is present


def test_format_comments_artifact_omits_diff_block_when_hunk_missing():
    artifact = format_comments_artifact([_comment(diff_hunk=None)])
    assert "```diff" not in artifact


def test_build_rules_prompt_mentions_diff_hunks():
    prompt = build_rules_prompt("repo", "ARTIFACT", existing_patterns=None)
    assert "diff hunk" in prompt.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rules_synthesizer.py::test_format_comments_artifact_renders_diff_hunk_when_present tests/unit/test_rules_synthesizer.py::test_build_rules_prompt_mentions_diff_hunks -v`
Expected: FAIL — formatter does not emit a diff fence; prompt does not mention diff hunks.

- [ ] **Step 3: Update `format_comments_artifact`**

In `bb_review/rules/synthesizer.py`, find `format_comments_artifact`. Replace the inner loop body. Currently:
```python
        for c in by_file[file_path]:
            loc = f":{c.line_number}" if c.line_number else ""
            status = c.issue_status or ("issue" if c.issue_opened else "comment")
            lines.append(
                f"- [RR #{c.rr_id} | {c.rr_status} | reviewer: {c.reviewer} | {status}] {file_path}{loc}"
            )
            body = c.text.strip().replace("\n", "\n  ")
            lines.append(f"  {body}")
```

Replace with:
```python
        for c in by_file[file_path]:
            loc = f":{c.line_number}" if c.line_number else ""
            status = c.issue_status or ("issue" if c.issue_opened else "comment")
            lines.append(
                f"- [RR #{c.rr_id} | {c.rr_status} | reviewer: {c.reviewer} | {status}] {file_path}{loc}"
            )
            body = c.text.strip().replace("\n", "\n  ")
            lines.append(f"  {body}")
            if c.diff_hunk:
                lines.append("  ```diff")
                for hunk_line in c.diff_hunk.splitlines():
                    lines.append(f"  {hunk_line}")
                lines.append("  ```")
```

- [ ] **Step 4: Update `build_rules_prompt`**

In `bb_review/rules/synthesizer.py`, find the "How to interpret the comments:" bullet list inside `build_rules_prompt`. After the line about `dropped` issues and before the "A pattern that recurs" line, add a new bullet. The current block:

```python
- `issue status = resolved` -> the author agreed and fixed it. These are \
confirmed mistakes and are strong rule candidates.
- `issue status = dropped` -> the author pushed back or disagreed. Treat \
these as weak signals and as false-positive candidates.
- A pattern that recurs across multiple distinct RRs matters more than a \
one-off remark.
```

becomes:

```python
- `issue status = resolved` -> the author agreed and fixed it. These are \
confirmed mistakes and are strong rule candidates.
- `issue status = dropped` -> the author pushed back or disagreed. Treat \
these as weak signals and as false-positive candidates.
- When a comment is followed by a fenced diff hunk, that hunk is the \
ground-truth code the reviewer was looking at -- use it to verify the rule \
before including it.
- A pattern that recurs across multiple distinct RRs matters more than a \
one-off remark.
```

- [ ] **Step 5: Add the `diff_hunk` keyword to the test helper**

In `tests/unit/test_rules_synthesizer.py`, find the `_comment` helper near the top:
```python
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
```

Replace with:
```python
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
        diff_revision=None,
        diff_hunk=None,
    )
    defaults.update(kw)
    return MinedComment(**defaults)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_rules_synthesizer.py -v`
Expected: PASS (all existing + three new).

- [ ] **Step 7: Commit**

```bash
git add bb_review/rules/synthesizer.py tests/unit/test_rules_synthesizer.py
git commit -m "feat: render diff hunks under comments in synthesis artifact"
```

---

## Task 8: CLI flag --with-diff-hunks

**Files:**
- Modify: `bb_review/cli/rules.py` (add `--with-diff-hunks` to `rules fetch`)
- Test: `tests/cli/test_rules.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/cli/test_rules.py`:
```python
def test_rules_fetch_forwards_with_diff_hunks(
    runner: CliRunner, config_path: Path, monkeypatch
):
    class StubRBClient:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            return None

    monkeypatch.setattr("bb_review.rr.rb_client.ReviewBoardClient", StubRBClient)

    captured = {}

    def fake_fetch(**kw):
        captured.update(kw)
        return {
            "total": 0,
            "fetched": 0,
            "skipped": 0,
            "comments": 0,
            "hunks_backfilled": 0,
        }

    monkeypatch.setattr("bb_review.cli.rules.fetch_repo_rules_data", fake_fetch)

    result = runner.invoke(
        main,
        ["--config", str(config_path), "rules", "fetch", "testrepo", "--with-diff-hunks"],
    )
    assert result.exit_code == 0
    assert captured["with_diff_hunks"] is True


def test_rules_fetch_reports_hunks_backfilled(
    runner: CliRunner, config_path: Path, monkeypatch
):
    class StubRBClient:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            return None

    monkeypatch.setattr("bb_review.rr.rb_client.ReviewBoardClient", StubRBClient)
    monkeypatch.setattr(
        "bb_review.cli.rules.fetch_repo_rules_data",
        lambda **kw: {
            "total": 3,
            "fetched": 1,
            "skipped": 1,
            "comments": 4,
            "hunks_backfilled": 1,
        },
    )
    result = runner.invoke(
        main,
        ["--config", str(config_path), "rules", "fetch", "testrepo", "--with-diff-hunks"],
    )
    assert result.exit_code == 0
    assert "1 hunks backfilled" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_rules.py::test_rules_fetch_forwards_with_diff_hunks tests/cli/test_rules.py::test_rules_fetch_reports_hunks_backfilled -v`
Expected: FAIL — Click rejects the `--with-diff-hunks` option / the summary line lacks the new counter.

- [ ] **Step 3: Add the CLI flag and update the summary**

In `bb_review/cli/rules.py`, find the `rules_fetch` command. Replace the entire command function from the `@rules.command("fetch")` decorator down through the existing `click.echo(...)` summary with:

```python
@rules.command("fetch")
@click.argument("repo_name")
@click.option("--count", default=30, help="Max recent review requests to mine.")
@click.option(
    "--days", default=0, help="Only mine RRs updated within N days (0 = no limit)."
)
@click.option("--refresh", is_flag=True, help="Re-fetch RRs even if already cached.")
@click.option(
    "--with-diff-hunks",
    is_flag=True,
    help=(
        "Also fetch and cache the diff hunk for each diff comment. "
        "Backfills missing hunks on already-cached RRs."
    ),
)
@click.pass_context
def rules_fetch(
    ctx: click.Context,
    repo_name: str,
    count: int,
    days: int,
    refresh: bool,
    with_diff_hunks: bool,
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
        with_diff_hunks=with_diff_hunks,
        on_progress=_progress,
    )
    click.echo()
    click.echo(
        f"Done: {counts['total']} RRs found, "
        f"{counts['fetched']} fetched, "
        f"{counts['skipped']} skipped, "
        f"{counts['comments']} comments cached, "
        f"{counts.get('hunks_backfilled', 0)} hunks backfilled."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_rules.py -v`
Expected: PASS (all existing + two new).

- [ ] **Step 5: Commit**

```bash
git add bb_review/cli/rules.py tests/cli/test_rules.py
git commit -m "feat: expose --with-diff-hunks on rules fetch CLI"
```

---

## Task 9: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit and CLI test suites**

Run: `uv run pytest tests/unit/ tests/cli/ -v`
Expected: PASS — all existing tests plus the new ones, no regressions.

- [ ] **Step 2: Run the linter**

Run: `task check`
Expected: `All checks passed!` for both `ruff check` and `ruff format --check`. If formatting differs, run `task format` and commit the result as a separate `chore: apply ruff formatting` commit.

- [ ] **Step 3: Smoke-test the CLI**

Run: `uv run bb-review rules fetch --help`
Expected: Help text now lists `--with-diff-hunks`.

- [ ] **Step 4: Commit any formatting fixes (only if Step 2 changed files)**

```bash
git add -u
git commit -m "chore: apply ruff formatting to diff-hunk changes"
```

If nothing changed, skip this step.

---

## Self-Review Notes

Spec coverage check against `docs/superpowers/specs/2026-05-20-rules-fetch-diff-hunk-design.md`:

- Schema change (two `ALTER TABLE`s + new columns in `CREATE`) — Task 1.
- `MinedComment` and `RBComment` dataclass fields — Task 1.
- `get_comments_missing_hunks` and `update_comment_diff_hunk` keyed by RB natural identifiers — Task 2.
- `RBCommentFetcher` populating `diff_revision` from the filediff href — Task 3.
- MockRBClient extension for per-(rr_id, rev) diff lookup — Task 4.
- Memoized per-(rr_id, rev) diff fetch + augmentation of new RRs — Task 5.
- Backfill semantics (cached RR, missing hunks, no comment re-fetch, `hunks_backfilled` counter) — Task 6.
- Artifact rendering with fenced `diff` blocks + prompt sentence — Task 7.
- CLI `--with-diff-hunks` flag + summary line update — Task 8.
- Error handling (extract_diff_hunk None, get_diff failure, missing revision) — Task 5 (negative-cache + skip) and Task 6 (filled-counter logic).
- Body comments excluded from backfill — Task 2 (the `file_path IS NOT NULL` guard in both query and update).
