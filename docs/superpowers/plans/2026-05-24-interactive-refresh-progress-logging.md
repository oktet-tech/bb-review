# Interactive Refresh Progress Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the silent gap in the interactive TUI's Refresh action with checkpoint lines, a throttled progress counter, and per-RR API-call notifications.

**Architecture:** Introduce a small `ProgressReporter` protocol. Three implementations: `NullProgressReporter` (default no-op), `TUIProgressReporter` (2s-throttled tick logging + title-bar updates via `call_from_thread`), and `ClickProgressReporter` (carriage-return-overwrite counter for CLI). Thread the reporter through `sync_queue`, `RB.get_recent_reviews`/`get_pending_reviews`, and `RBCommentFetcher.fetch_all_comments`, replacing the existing `on_progress` callback parameters.

**Tech Stack:** Python 3.10+, pytest, Textual (TUI), Click (CLI).

**Spec:** `docs/superpowers/specs/2026-05-24-interactive-refresh-progress-logging-design.md`

---

## File Map

**Create:**
- `bb_review/progress.py` — `ProgressReporter` Protocol + `NullProgressReporter`
- `bb_review/ui/progress_reporter.py` — `TUIProgressReporter`
- `bb_review/cli/_progress.py` — `ClickProgressReporter`
- `tests/unit/test_progress.py` — tests for `TUIProgressReporter` + `NullProgressReporter`
- `tests/unit/test_progress_cli.py` — tests for `ClickProgressReporter`
- `tests/unit/test_rb_client_progress.py` — reporter wiring in `get_recent_reviews` / `get_pending_reviews`
- `tests/unit/test_rb_fetcher_progress.py` — reporter wiring in `RBCommentFetcher.fetch_all_comments`
- `tests/unit/test_queue_sync_progress.py` — reporter wiring in `sync_queue` / `_sync_one`

**Modify:**
- `bb_review/rr/rb_client.py` — replace `on_progress` with `reporter` on `get_recent_reviews` and `get_pending_reviews`; add pre/post checkpoints
- `bb_review/rr/rb_fetcher.py` — add `reporter` parameter to `fetch_all_comments`
- `bb_review/queue_sync.py` — replace `on_progress` with `reporter`; emit reconcile/prune checkpoints; thread reporter into `_sync_one` to emit per-RR `diffs_equal` item events
- `bb_review/ui/unified_app.py` — three workers (`_run_sync`, `_run_my_reviews_sync`, `_run_fetch_issues`) build a `TUIProgressReporter` and pass it down
- `bb_review/cli/queue.py` — pass a `ClickProgressReporter` instead of the local `_progress` lambda

**Untouched callers (pass nothing, get `NullProgressReporter` default):**
- `bb_review/cli/poll.py`, `bb_review/cli/comments.py`, `bb_review/cli/triage.py`, `bb_review/cli/resolve.py`

---

## Task 1: ProgressReporter protocol and null reporter

**Files:**
- Create: `bb_review/progress.py`

- [ ] **Step 1: Create the protocol module**

Create `bb_review/progress.py`:

```python
"""Progress reporting protocol for long-running sync operations.

A ProgressReporter is a small interface that sync code can call to surface
progress events. The presentation (logging, throttling, CLI overwrite, etc.)
is the reporter's responsibility — sync code just emits events.
"""

from typing import Protocol


class ProgressReporter(Protocol):
    """Receives progress events from long-running sync operations."""

    def checkpoint(self, msg: str) -> None:
        """A phase transition. Reporters surface this immediately."""
        ...

    def tick(self, current: int, total: int) -> None:
        """Per-item progress. Reporters choose their own cadence."""
        ...

    def item_event(self, msg: str) -> None:
        """A notable event for one item. Reporters surface this immediately."""
        ...


class NullProgressReporter:
    """No-op reporter used as the default when callers don't pass one."""

    def checkpoint(self, msg: str) -> None:
        pass

    def tick(self, current: int, total: int) -> None:
        pass

    def item_event(self, msg: str) -> None:
        pass
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `uv run python -c "from bb_review.progress import ProgressReporter, NullProgressReporter; r = NullProgressReporter(); r.checkpoint('x'); r.tick(1, 2); r.item_event('y'); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add bb_review/progress.py
git commit -m "feat(progress): add ProgressReporter protocol and null reporter"
```

---

## Task 2: TUIProgressReporter (throttled, thread-safe)

**Files:**
- Create: `bb_review/ui/progress_reporter.py`
- Test: `tests/unit/test_progress.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_progress.py`:

```python
"""Tests for TUIProgressReporter (throttled, thread-safe progress)."""

from types import SimpleNamespace

from bb_review.ui.progress_reporter import TUIProgressReporter


class _FakeApp:
    """Stand-in for UnifiedApp: records calls instead of touching the UI."""

    def __init__(self):
        self.log_lines: list[str] = []
        self.task_updates: list[tuple[str, str]] = []

    def call_from_thread(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def _log(self, text: str) -> None:
        self.log_lines.append(text)

    def _task_start(self, key: str, label: str) -> None:
        self.task_updates.append((key, label))


def _make_reporter(app, clock):
    return TUIProgressReporter(
        app=app,
        task_key='sync',
        label='sync',
        min_tick_interval=2.0,
        clock=clock,
    )


def test_checkpoint_logs_immediately():
    app = _FakeApp()
    reporter = _make_reporter(app, clock=lambda: 0.0)
    reporter.checkpoint('Fetching from RB...')
    assert app.log_lines == ['Fetching from RB...']


def test_item_event_logs_immediately():
    app = _FakeApp()
    reporter = _make_reporter(app, clock=lambda: 0.0)
    reporter.item_event('r/123: checking diff 1->2...')
    assert app.log_lines == ['r/123: checking diff 1->2...']


def test_tick_always_updates_title_bar():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)
    now[0] = 0.5
    reporter.tick(2, 100)
    now[0] = 0.6
    reporter.tick(3, 100)
    assert app.task_updates == [
        ('sync', 'sync 1/100'),
        ('sync', 'sync 2/100'),
        ('sync', 'sync 3/100'),
    ]


def test_first_tick_always_logs():
    app = _FakeApp()
    reporter = _make_reporter(app, clock=lambda: 0.0)
    reporter.tick(1, 100)
    assert app.log_lines == ['Processed 1/100']


def test_tick_throttled_within_window():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)            # t=0.0 -> logs
    now[0] = 0.5
    reporter.tick(2, 100)            # t=0.5 -> suppressed
    now[0] = 1.9
    reporter.tick(3, 100)            # t=1.9 -> suppressed
    assert app.log_lines == ['Processed 1/100']


def test_tick_logs_again_after_window():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)            # t=0.0 -> logs
    now[0] = 2.1
    reporter.tick(50, 100)           # t=2.1 -> logs again
    assert app.log_lines == ['Processed 1/100', 'Processed 50/100']


def test_final_tick_always_logs_even_when_throttled():
    app = _FakeApp()
    now = [0.0]
    reporter = _make_reporter(app, clock=lambda: now[0])
    reporter.tick(1, 100)            # t=0.0 -> logs
    now[0] = 0.5
    reporter.tick(100, 100)          # t=0.5 (within window) -> still logs (final)
    assert app.log_lines == ['Processed 1/100', 'Processed 100/100']


def test_label_used_in_title_bar():
    app = _FakeApp()
    reporter = TUIProgressReporter(
        app=app,
        task_key='my_reviews_sync',
        label='my-sync',
        min_tick_interval=2.0,
        clock=lambda: 0.0,
    )
    reporter.tick(5, 20)
    assert app.task_updates == [('my_reviews_sync', 'my-sync 5/20')]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bb_review.ui.progress_reporter'`

- [ ] **Step 3: Implement TUIProgressReporter**

Create `bb_review/ui/progress_reporter.py`:

```python
"""Throttled, thread-safe progress reporter for the Textual TUI."""

from collections.abc import Callable
import time
from typing import Any


class TUIProgressReporter:
    """Routes ProgressReporter events into a Textual app.

    - ``checkpoint`` / ``item_event``: always logged immediately via ``app._log``.
    - ``tick``: always updates the title bar via ``app._task_start``; logs to
      the log panel at most once per ``min_tick_interval`` seconds, plus
      always on the final tick (``current == total``).

    All UI calls are dispatched via ``app.call_from_thread`` so the reporter
    is safe to call from background worker threads.
    """

    def __init__(
        self,
        app: Any,
        task_key: str,
        label: str,
        min_tick_interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._app = app
        self._task_key = task_key
        self._label = label
        self._min_tick_interval = min_tick_interval
        self._clock = clock
        # Initialize so the first tick always falls outside the window.
        self._last_tick_at = -min_tick_interval

    def checkpoint(self, msg: str) -> None:
        self._app.call_from_thread(self._app._log, msg)

    def item_event(self, msg: str) -> None:
        self._app.call_from_thread(self._app._log, msg)

    def tick(self, current: int, total: int) -> None:
        self._app.call_from_thread(
            self._app._task_start,
            self._task_key,
            f'{self._label} {current}/{total}',
        )
        now = self._clock()
        is_final = current == total
        if is_final or (now - self._last_tick_at) >= self._min_tick_interval:
            self._app.call_from_thread(
                self._app._log,
                f'Processed {current}/{total}',
            )
            self._last_tick_at = now
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_progress.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add bb_review/ui/progress_reporter.py tests/unit/test_progress.py
git commit -m "feat(progress): add TUIProgressReporter with 2s tick throttle"
```

---

## Task 3: ClickProgressReporter (CLI carriage-return counter)

**Files:**
- Create: `bb_review/cli/_progress.py`
- Test: `tests/unit/test_progress_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_progress_cli.py`:

```python
"""Tests for ClickProgressReporter (CLI carriage-return counter)."""

import click
from click.testing import CliRunner

from bb_review.cli._progress import ClickProgressReporter


def _run(action) -> str:
    """Run ``action`` inside a Click command and capture stdout."""

    @click.command()
    def cmd():
        action()

    runner = CliRunner()
    result = runner.invoke(cmd, [])
    assert result.exit_code == 0, result.output
    return result.output


def test_checkpoint_writes_line():
    def go():
        ClickProgressReporter().checkpoint('Fetching from RB...')

    out = _run(go)
    assert out == 'Fetching from RB...\n'


def test_item_event_writes_line():
    def go():
        ClickProgressReporter().item_event('r/123: checking diff 1->2...')

    out = _run(go)
    assert out == 'r/123: checking diff 1->2...\n'


def test_tick_overwrites_with_cr():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 5)
        r.tick(2, 5)
        r.tick(3, 5)

    out = _run(go)
    # Each tick overwrites; no trailing newline until something else fires.
    assert out == '\rFetching: 1/5...\rFetching: 2/5...\rFetching: 3/5...'


def test_checkpoint_after_tick_inserts_newline():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 5)
        r.checkpoint('Reconciling...')

    out = _run(go)
    assert out == '\rFetching: 1/5...\nReconciling...\n'


def test_item_event_after_tick_inserts_newline():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 5)
        r.item_event('r/123: checking diff 1->2...')

    out = _run(go)
    assert out == '\rFetching: 1/5...\nr/123: checking diff 1->2...\n'


def test_final_tick_emits_newline():
    def go():
        r = ClickProgressReporter()
        r.tick(1, 2)
        r.tick(2, 2)

    out = _run(go)
    # Final tick (current == total) appends a newline so subsequent
    # non-tick output starts on a fresh row.
    assert out == '\rFetching: 1/2...\rFetching: 2/2...\n'


def test_consecutive_non_tick_calls_do_not_insert_extra_newline():
    def go():
        r = ClickProgressReporter()
        r.checkpoint('A')
        r.checkpoint('B')

    out = _run(go)
    assert out == 'A\nB\n'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_progress_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bb_review.cli._progress'`

- [ ] **Step 3: Implement ClickProgressReporter**

Create `bb_review/cli/_progress.py`:

```python
"""ProgressReporter implementation for Click-based CLI commands.

Preserves the single-line carriage-return counter UX that ``cli/queue.py``
used before the reporter refactor, while adding checkpoint and item-event
lines from the same source.
"""

import click


class ClickProgressReporter:
    """Writes progress events to stdout via Click.

    ``tick`` overwrites a single line using ``\\r`` (unthrottled — terminal
    overwrites are cheap and a stable counter feels better than a 2s jump).
    ``checkpoint`` and ``item_event`` always write on their own line, and
    insert a trailing newline first if the previous output was a tick.
    The final tick (``current == total``) also emits the trailing newline so
    subsequent caller output starts on a fresh row.
    """

    def __init__(self):
        self._last_was_tick = False

    def checkpoint(self, msg: str) -> None:
        self._flush_tick_line()
        click.echo(msg)

    def item_event(self, msg: str) -> None:
        self._flush_tick_line()
        click.echo(msg)

    def tick(self, current: int, total: int) -> None:
        click.echo(f'\rFetching: {current}/{total}...', nl=False)
        self._last_was_tick = True
        if current == total:
            click.echo()
            self._last_was_tick = False

    def _flush_tick_line(self) -> None:
        if self._last_was_tick:
            click.echo()
            self._last_was_tick = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_progress_cli.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add bb_review/cli/_progress.py tests/unit/test_progress_cli.py
git commit -m "feat(progress): add ClickProgressReporter for CLI commands"
```

---

## Task 4: Wire reporter into RB.get_recent_reviews / get_pending_reviews

Replaces the `on_progress` parameter with a `reporter: ProgressReporter | None`. Adds pre-call and post-call checkpoints, ticks per RR in the loop.

**Files:**
- Modify: `bb_review/rr/rb_client.py` (signatures + bodies of `get_recent_reviews` and `get_pending_reviews`)
- Test: `tests/unit/test_rb_client_progress.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rb_client_progress.py`:

```python
"""Tests that get_recent_reviews / get_pending_reviews emit reporter events."""

from bb_review.models import PendingReview
from bb_review.rr.rb_client import ReviewBoardClient


class _RecordingReporter:
    """Captures ProgressReporter events as a list of tuples."""

    def __init__(self):
        self.events: list[tuple] = []

    def checkpoint(self, msg):
        self.events.append(('checkpoint', msg))

    def tick(self, current, total):
        self.events.append(('tick', current, total))

    def item_event(self, msg):
        self.events.append(('item', msg))


def _fake_pending_review(rr_id: int) -> PendingReview:
    return PendingReview(
        review_request_id=rr_id,
        repository='test-repo',
        submitter='dev',
        summary=f'rr {rr_id}',
        diff_revision=1,
        base_commit='abc123',
        branch='main',
        created_at=None,
        issue_open_count=0,
        ship_it_count=0,
    )


def _install_three_rrs(client, monkeypatch):
    """Make client.get_*_reviews see 3 RRs, skipping the real API/hydration."""
    monkeypatch.setattr(
        client,
        '_api_get',
        lambda path, params=None: {
            'review_requests': [{'id': 101}, {'id': 102}, {'id': 103}],
        },
    )
    monkeypatch.setattr(
        client,
        '_to_pending_review',
        lambda rr: _fake_pending_review(rr['id']),
    )


def test_get_recent_reviews_emits_checkpoints_and_ticks(monkeypatch):
    client = ReviewBoardClient(url='https://rb.example.com', bot_username='bot')
    _install_three_rrs(client, monkeypatch)
    reporter = _RecordingReporter()

    result = client.get_recent_reviews(days=10, limit=200, reporter=reporter)

    assert [pr.review_request_id for pr in result] == [101, 102, 103]
    assert reporter.events == [
        ('checkpoint', 'Fetching review requests from RB (last 10 days, max 200)...'),
        ('checkpoint', 'Got 3 review requests from RB, hydrating...'),
        ('tick', 1, 3),
        ('tick', 2, 3),
        ('tick', 3, 3),
    ]


def test_get_recent_reviews_works_without_reporter(monkeypatch):
    client = ReviewBoardClient(url='https://rb.example.com', bot_username='bot')
    _install_three_rrs(client, monkeypatch)
    # No reporter passed — must not crash.
    result = client.get_recent_reviews(days=10, limit=200)
    assert len(result) == 3


def test_get_pending_reviews_emits_checkpoints_and_ticks(monkeypatch):
    client = ReviewBoardClient(url='https://rb.example.com', bot_username='bot')
    monkeypatch.setattr(
        client,
        '_api_get',
        lambda path, params=None: {
            'review_requests': [{'id': 201}, {'id': 202}],
        },
    )
    monkeypatch.setattr(client, '_has_bot_reviewed', lambda _rr_id: False)
    monkeypatch.setattr(
        client,
        '_to_pending_review',
        lambda rr: _fake_pending_review(rr['id']),
    )
    reporter = _RecordingReporter()

    result = client.get_pending_reviews(limit=50, reporter=reporter)

    assert [pr.review_request_id for pr in result] == [201, 202]
    assert reporter.events == [
        ('checkpoint', 'Fetching pending reviews assigned to bot...'),
        ('checkpoint', 'Got 2 review requests from RB, hydrating...'),
        ('tick', 1, 2),
        ('tick', 2, 2),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rb_client_progress.py -v`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'reporter'`

- [ ] **Step 3: Update get_recent_reviews and get_pending_reviews**

In `bb_review/rr/rb_client.py`:

Replace the `get_pending_reviews` method (currently lines 309-341):

```python
    def get_pending_reviews(
        self,
        limit: int = 50,
        reporter: 'ProgressReporter | None' = None,
    ) -> list[PendingReview]:
        """Get review requests where bot user is a reviewer but hasn't reviewed."""
        from ..progress import NullProgressReporter

        reporter = reporter or NullProgressReporter()
        logger.debug(f'Fetching pending reviews for {self.bot_username}')

        reporter.checkpoint('Fetching pending reviews assigned to bot...')
        result = self._api_get(
            '/api/review-requests/',
            {
                'to-users': self.bot_username,
                'status': 'pending',
                'max-results': str(limit),
            },
        )

        review_requests = result.get('review_requests', [])
        total = len(review_requests)
        reporter.checkpoint(f'Got {total} review requests from RB, hydrating...')
        pending = []

        for i, rr in enumerate(review_requests):
            if self._has_bot_reviewed(rr['id']):
                logger.debug(f"Skipping {rr['id']} - already reviewed")
            else:
                pending_review = self._to_pending_review(rr)
                if pending_review:
                    pending.append(pending_review)
            reporter.tick(i + 1, total)

        logger.info(f'Found {len(pending)} pending reviews')
        return pending
```

Replace the `get_recent_reviews` method (currently lines 711-753):

```python
    def get_recent_reviews(
        self,
        days: int = 10,
        limit: int = 200,
        repository: str | None = None,
        from_user: str | None = None,
        reporter: 'ProgressReporter | None' = None,
    ) -> list[PendingReview]:
        """Fetch recently-updated pending review requests from RB.

        Args:
            days: How far back to look (via last-updated-from).
            limit: Max results to return.
            repository: Filter by repository name (RB repo ID or name).
            from_user: Filter by submitter username.
            reporter: Optional ProgressReporter for surfacing progress.
        """
        from ..progress import NullProgressReporter

        reporter = reporter or NullProgressReporter()
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        params: dict[str, str] = {
            'status': 'pending',
            'last-updated-from': cutoff,
            'max-results': str(limit),
        }
        if repository:
            params['repository'] = repository
        if from_user:
            params['from-user'] = from_user

        logger.debug(f'Fetching recent reviews: days={days}, limit={limit}')
        reporter.checkpoint(
            f'Fetching review requests from RB (last {days} days, max {limit})...'
        )
        result = self._api_get('/api/review-requests/', params)
        review_requests = result.get('review_requests', [])
        total = len(review_requests)
        reporter.checkpoint(f'Got {total} review requests from RB, hydrating...')

        pending = []
        for i, rr in enumerate(review_requests):
            pr = self._to_pending_review(rr)
            if pr:
                pending.append(pr)
            reporter.tick(i + 1, total)

        logger.info(f'Fetched {len(pending)} recent reviews')
        return pending
```

Note: this codebase uses single quotes everywhere (per CLAUDE.md). Match the existing style in any further edits to `rb_client.py`.

- [ ] **Step 4: Run new tests to verify they pass**

Run: `uv run pytest tests/unit/test_rb_client_progress.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full rb_client test suite to ensure no regressions**

Run: `uv run pytest tests/unit/test_rb_client.py -v`
Expected: PASS (no regressions — these tests didn't use `on_progress`)

- [ ] **Step 6: Commit**

```bash
git add bb_review/rr/rb_client.py tests/unit/test_rb_client_progress.py
git commit -m "refactor(rb_client): replace on_progress with reporter, add checkpoints"
```

---

## Task 5: Wire reporter into RBCommentFetcher.fetch_all_comments

**Files:**
- Modify: `bb_review/rr/rb_fetcher.py` (`fetch_all_comments` signature + body)
- Test: `tests/unit/test_rb_fetcher_progress.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rb_fetcher_progress.py`:

```python
"""Tests that RBCommentFetcher.fetch_all_comments emits reporter events."""

from bb_review.rr.rb_fetcher import RBCommentFetcher


class _RecordingReporter:
    def __init__(self):
        self.events: list[tuple] = []

    def checkpoint(self, msg):
        self.events.append(('checkpoint', msg))

    def tick(self, current, total):
        self.events.append(('tick', current, total))

    def item_event(self, msg):
        self.events.append(('item', msg))


class _StubRBClient:
    """Minimal stub that returns N reviews with no diff comments or replies."""

    def __init__(self, review_ids: list[int]):
        self._review_ids = review_ids
        self._filediff_cache: dict[int, list] = {}

    def get_reviews(self, rr_id):
        return [
            {
                'id': rid,
                'body_top': '',
                'links': {'user': {'href': '/api/users/dev/'}},
            }
            for rid in self._review_ids
        ]

    def _warm_filediff_cache(self, rr_id):
        pass

    def get_review_diff_comments(self, rr_id, review_id):
        return []

    def get_review_replies(self, rr_id, review_id):
        return []


def test_fetch_all_comments_emits_checkpoint_and_ticks():
    client = _StubRBClient(review_ids=[10, 11, 12])
    fetcher = RBCommentFetcher(client, bot_username='bot')
    reporter = _RecordingReporter()

    comments = fetcher.fetch_all_comments(rr_id=42, reporter=reporter)

    assert comments == []  # stub returns no body/diff/reply content
    assert reporter.events == [
        ('checkpoint', 'Fetching comments for r/42...'),
        ('tick', 1, 3),
        ('tick', 2, 3),
        ('tick', 3, 3),
    ]


def test_fetch_all_comments_works_without_reporter():
    client = _StubRBClient(review_ids=[10])
    fetcher = RBCommentFetcher(client, bot_username='bot')
    # No reporter passed — must not crash.
    comments = fetcher.fetch_all_comments(rr_id=42)
    assert comments == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rb_fetcher_progress.py -v`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'reporter'`

- [ ] **Step 3: Update fetch_all_comments**

In `bb_review/rr/rb_fetcher.py`, replace the `fetch_all_comments` method:

```python
    def fetch_all_comments(
        self,
        rr_id: int,
        include_bot: bool = False,
        reporter: 'ProgressReporter | None' = None,
    ) -> list[RBComment]:
        """Fetch all comments for a review request.

        Returns a flat list of RBComment covering:
        - body_top text from each review (as body comments)
        - diff-level inline comments from each review
        Replies are included with reply_to_id set.

        Args:
            rr_id: Review request ID.
            include_bot: If True, include comments from the bot user.
            reporter: Optional ProgressReporter for surfacing progress.
        """
        from ..progress import NullProgressReporter

        reporter = reporter or NullProgressReporter()
        reporter.checkpoint(f'Fetching comments for r/{rr_id}...')

        reviews = self.rb_client.get_reviews(rr_id)
        comments: list[RBComment] = []

        # Pre-warm filediff cache for resolving file paths from filediff links
        self.rb_client._warm_filediff_cache(rr_id)

        total = len(reviews)
        for i, review in enumerate(reviews):
            reviewer = self._extract_username(review)
            if not include_bot and reviewer == self.bot_username:
                logger.debug(f"Skipping bot review {review.get('id')}")
                reporter.tick(i + 1, total)
                continue

            review_id = review['id']

            # Body top as a body comment
            body_top = (review.get('body_top') or '').strip()
            if body_top:
                comments.append(
                    RBComment(
                        review_id=review_id,
                        comment_id=review_id,  # use review_id as identifier for body comments
                        reviewer=reviewer,
                        text=body_top,
                        is_body_comment=True,
                        issue_opened=False,
                    )
                )

            # Diff comments
            diff_comments = self.rb_client.get_review_diff_comments(rr_id, review_id)
            for dc in diff_comments:
                file_path = self._resolve_file_path(rr_id, dc)
                diff_revision = self._resolve_diff_revision(dc)
                comments.append(
                    RBComment(
                        review_id=review_id,
                        comment_id=dc['id'],
                        reviewer=reviewer,
                        text=dc.get('text', ''),
                        file_path=file_path,
                        line_number=dc.get('first_line'),
                        issue_opened=dc.get('issue_opened', False),
                        issue_status=dc.get('issue_status'),
                        diff_revision=diff_revision,
                    )
                )

            # Replies to this review
            replies = self.rb_client.get_review_replies(rr_id, review_id)
            for reply in replies:
                reply_reviewer = self._extract_username(reply)
                if not include_bot and reply_reviewer == self.bot_username:
                    continue

                reply_body = (reply.get('body_top') or '').strip()
                if reply_body:
                    comments.append(
                        RBComment(
                            review_id=review_id,
                            comment_id=reply['id'],
                            reviewer=reply_reviewer,
                            text=reply_body,
                            is_body_comment=True,
                            reply_to_id=review_id,
                        )
                    )

            reporter.tick(i + 1, total)

        logger.info(f'Fetched {len(comments)} comments for RR #{rr_id}')
        return comments
```

Note the body of the method now uses single quotes throughout (CLAUDE.md style — the original mixed single and double quotes; clean up while modifying).

- [ ] **Step 4: Run new tests to verify they pass**

Run: `uv run pytest tests/unit/test_rb_fetcher_progress.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run any existing fetcher-touching tests for regressions**

Run: `uv run pytest tests/unit/ -k "fetch_all_comments or rb_fetcher" -v`
Expected: PASS (existing tests that call `fetch_all_comments` without `reporter` keep working — the kwarg is optional)

- [ ] **Step 6: Commit**

```bash
git add bb_review/rr/rb_fetcher.py tests/unit/test_rb_fetcher_progress.py
git commit -m "feat(rb_fetcher): emit progress reporter events from fetch_all_comments"
```

---

## Task 6: Wire reporter into sync_queue and _sync_one

Replaces `on_progress` with `reporter`. Adds:
- "Reconciling N..." checkpoint after the fetch (only when N > 0)
- "Pruning..." checkpoint when pruning has anything to scan
- Per-RR `item_event` just before each `diffs_equal` call inside `_sync_one`

**Files:**
- Modify: `bb_review/queue_sync.py`
- Test: `tests/unit/test_queue_sync_progress.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_queue_sync_progress.py`:

```python
"""Tests that sync_queue and _sync_one emit reporter events."""

from datetime import datetime
from pathlib import Path

import pytest

from bb_review.db.queue_db import QueueDatabase
from bb_review.models import PendingReview
from bb_review.queue_sync import sync_queue


class _RecordingReporter:
    def __init__(self):
        self.events: list[tuple] = []

    def checkpoint(self, msg):
        self.events.append(('checkpoint', msg))

    def tick(self, current, total):
        self.events.append(('tick', current, total))

    def item_event(self, msg):
        self.events.append(('item', msg))


class _FakeRBClient:
    """Minimal RB client stand-in for sync_queue tests.

    Returns the configured PendingReviews from get_recent_reviews / get_pending_reviews.
    diffs_equal can be configured to return False (signals a real content change).
    """

    def __init__(self, pending: list[PendingReview], diffs_equal_returns: bool = True):
        self._pending = pending
        self._diffs_equal_returns = diffs_equal_returns

    def get_recent_reviews(self, days, limit, repository=None, from_user=None, reporter=None):
        # Mirror the real method's reporter usage so the reporter sees ticks.
        from bb_review.progress import NullProgressReporter
        reporter = reporter or NullProgressReporter()
        reporter.checkpoint(
            f'Fetching review requests from RB (last {days} days, max {limit})...'
        )
        total = len(self._pending)
        reporter.checkpoint(f'Got {total} review requests from RB, hydrating...')
        for i in range(total):
            reporter.tick(i + 1, total)
        return list(self._pending)

    def get_pending_reviews(self, limit, reporter=None):
        return list(self._pending)

    def diffs_equal(self, rr_id, rev_a, rev_b):
        return self._diffs_equal_returns


def _make_pending(rr_id: int, diff_revision: int = 1) -> PendingReview:
    return PendingReview(
        review_request_id=rr_id,
        repository='test-repo',
        submitter='dev',
        summary=f'rr {rr_id}',
        diff_revision=diff_revision,
        base_commit='abc123',
        branch='main',
        created_at=datetime(2026, 5, 24),
        issue_open_count=0,
        ship_it_count=0,
    )


@pytest.fixture
def queue_db(tmp_path: Path) -> QueueDatabase:
    return QueueDatabase(tmp_path / 'queue.db')


def test_sync_queue_emits_reconcile_checkpoint(queue_db: QueueDatabase):
    pending = [_make_pending(101), _make_pending(102), _make_pending(103)]
    client = _FakeRBClient(pending)
    reporter = _RecordingReporter()

    sync_queue(rb_client=client, queue_db=queue_db, days=10, reporter=reporter)

    kinds = [e[0] for e in reporter.events]
    assert ('checkpoint', 'Reconciling 3 review requests against local queue...') in reporter.events
    # Ticks come from the (faked) get_recent_reviews.
    assert kinds.count('tick') == 3


def test_sync_queue_skips_reconcile_checkpoint_when_empty(queue_db: QueueDatabase):
    client = _FakeRBClient(pending=[])
    reporter = _RecordingReporter()

    sync_queue(rb_client=client, queue_db=queue_db, days=10, reporter=reporter)

    reconcile_msgs = [
        msg for kind, msg in [(e[0], e[1] if len(e) > 1 else None) for e in reporter.events]
        if kind == 'checkpoint' and msg and 'Reconciling' in msg
    ]
    assert reconcile_msgs == []


def test_sync_queue_emits_item_event_when_diff_revision_changes(queue_db: QueueDatabase):
    # First sync: rr 200 enters at diff_revision=1.
    client1 = _FakeRBClient([_make_pending(200, diff_revision=1)])
    sync_queue(rb_client=client1, queue_db=queue_db, days=10)

    # Second sync: same rr now at diff_revision=2 with a real content change.
    client2 = _FakeRBClient(
        [_make_pending(200, diff_revision=2)],
        diffs_equal_returns=False,
    )
    reporter = _RecordingReporter()
    sync_queue(rb_client=client2, queue_db=queue_db, days=10, reporter=reporter, prune=False)

    item_events = [e for e in reporter.events if e[0] == 'item']
    assert item_events == [
        ('item', 'r/200: checking diff 1->2 for content change...'),
    ]


def test_sync_queue_works_without_reporter(queue_db: QueueDatabase):
    client = _FakeRBClient([_make_pending(101)])
    # No reporter passed — must not crash.
    counts = sync_queue(rb_client=client, queue_db=queue_db, days=10)
    assert counts['total'] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_queue_sync_progress.py -v`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'reporter'`

- [ ] **Step 3: Update sync_queue and _sync_one signatures**

In `bb_review/queue_sync.py`, replace the file's contents (the diff is contained and easier as a full rewrite — it stays under 200 lines):

```python
"""Sync logic: fetch review requests from RB and reconcile with queue."""

import logging

from .db.queue_db import QueueDatabase
from .db.queue_models import QueueItem, QueueStatus
from .models import PendingReview
from .progress import NullProgressReporter, ProgressReporter
from .rr.rb_client import ReviewBoardClient


logger = logging.getLogger(__name__)

# Items in these statuses are pruned when no longer on RB
_PRUNABLE_STATUSES = {QueueStatus.TODO, QueueStatus.NEXT, QueueStatus.IGNORE}


def sync_queue(
    rb_client: ReviewBoardClient,
    queue_db: QueueDatabase,
    days: int = 10,
    limit: int = 200,
    repository: str | None = None,
    submitter: str | None = None,
    bot_only: bool = False,
    prune: bool = True,
    reporter: ProgressReporter | None = None,
) -> dict[str, int]:
    """Fetch recent RRs from Review Board and reconcile with the queue.

    Sync rules per fetched RR:
    1. Not in queue -> INSERT as todo
    2. In queue, same diff_revision, has non-fake analysis -> skip
    3. In queue, same diff_revision, no non-fake analysis -> keep status (metadata update)
    4. In queue, new diff_revision -> reset to todo, clear analysis_id

    When prune=True, queue items with status in (todo, next, ignore) that are
    no longer present in the fetched set are deleted. This handles RRs that
    were submitted, discarded, or otherwise removed from RB.

    Args:
        rb_client: Connected RB client.
        queue_db: Queue database instance.
        days: How far back to look.
        limit: Max RRs to fetch.
        repository: Filter by repository.
        submitter: Filter by submitter username.
        bot_only: If True, only fetch RRs assigned to the bot user.
        prune: If True, delete queue items no longer on RB.
        reporter: Optional ProgressReporter for surfacing progress.

    Returns:
        Dict with counts: inserted, updated (reset), skipped, total, pruned.
    """
    reporter = reporter or NullProgressReporter()
    from_user = submitter
    if bot_only:
        pending = rb_client.get_pending_reviews(limit=limit, reporter=reporter)
    else:
        pending = rb_client.get_recent_reviews(
            days=days,
            limit=limit,
            repository=repository,
            from_user=from_user,
            reporter=reporter,
        )

    counts = {
        'inserted': 0,
        'updated': 0,
        'skipped': 0,
        'analyzed': 0,
        'total': len(pending),
        'pruned': 0,
    }

    if pending:
        reporter.checkpoint(
            f'Reconciling {len(pending)} review requests against local queue...'
        )

    for pr in pending:
        _sync_one(rb_client, queue_db, pr, counts, reporter)

    if prune:
        fetched_rr_ids = {pr.review_request_id for pr in pending}
        # Only emit the checkpoint when we actually have items to scan against.
        if queue_db.list_items(limit=1):
            reporter.checkpoint('Pruning items no longer on RB...')
        counts['pruned'] = _prune_gone(queue_db, fetched_rr_ids)

    return counts


def _prune_gone(queue_db: QueueDatabase, fetched_rr_ids: set[int]) -> int:
    """Delete queue items that are no longer present on RB.

    Only prunes items with prunable statuses (todo, next, ignore).
    Items that are in_progress, done, or failed are kept.
    """
    all_items = queue_db.list_items(limit=10000)
    pruned = 0

    for item in all_items:
        if item.review_request_id not in fetched_rr_ids and item.status in _PRUNABLE_STATUSES:
            queue_db.delete_item(item.review_request_id)
            logger.info(f'r/{item.review_request_id}: pruned (no longer on RB)')
            pruned += 1

    return pruned


def _classify_change(existing: QueueItem | None, pr: PendingReview) -> str:
    """Determine what changed between the stored snapshot and fresh RB data."""
    if existing is None:
        return ''
    # Use > not != — if _get_latest_diff_revision transiently fails and
    # returns 0, we'd false-positive on every synced item.
    if pr.diff_revision > existing.diff_revision and existing.diff_revision > 0:
        return 'new_diff'
    if pr.issue_open_count > existing.issue_open_count:
        return 'issues_opened'
    if pr.issue_open_count < existing.issue_open_count:
        return 'issues_closed'
    if pr.ship_it_count > existing.ship_it_count:
        return 'ship_it'
    return ''


def _sync_one(
    rb_client: ReviewBoardClient,
    queue_db: QueueDatabase,
    pr: PendingReview,
    counts: dict[str, int],
    reporter: ProgressReporter,
) -> None:
    """Reconcile a single PendingReview with the queue."""
    existing = queue_db.get(pr.review_request_id)

    # _get_latest_diff_revision returns 0 on API timeout — don't let that
    # overwrite a real stored value and trigger a false "new diff" reset.
    if existing and pr.diff_revision == 0 and existing.diff_revision > 0:
        logger.debug(
            f'r/{pr.review_request_id}: API returned diff_revision=0, '
            f'keeping stored={existing.diff_revision}'
        )
        pr.diff_revision = existing.diff_revision

    change_reason = _classify_change(existing, pr)

    # Distinguish commit-message-only updates from real code changes.
    if change_reason == 'new_diff' and existing:
        reporter.item_event(
            f'r/{pr.review_request_id}: checking diff '
            f'{existing.diff_revision}->{pr.diff_revision} for content change...'
        )
        if rb_client.diffs_equal(pr.review_request_id, existing.diff_revision, pr.diff_revision):
            change_reason = 'new_msg'
            logger.info(
                f'r/{pr.review_request_id}: diff '
                f'{existing.diff_revision}->{pr.diff_revision} is message-only'
            )

    # Check if there's already a non-fake analysis for this exact diff.
    if existing and existing.diff_revision == pr.diff_revision:
        has_analysis = queue_db.has_non_fake_analysis(
            pr.review_request_id,
            pr.diff_revision,
        )
        if has_analysis:
            counts['analyzed'] += 1
            logger.debug(
                f'r/{pr.review_request_id}: already analyzed '
                f'(diff {pr.diff_revision}), skipping'
            )
            # Still update metadata and change_reason.
            queue_db.upsert(
                review_request_id=pr.review_request_id,
                diff_revision=pr.diff_revision,
                repository=pr.repository,
                submitter=pr.submitter,
                summary=pr.summary,
                branch=pr.branch,
                base_commit=pr.base_commit,
                rb_created_at=pr.created_at,
                issue_open_count=pr.issue_open_count,
                ship_it_count=pr.ship_it_count,
                change_reason=change_reason,
            )
            return

    skip_reset = change_reason == 'new_msg'
    action, reset = queue_db.upsert(
        review_request_id=pr.review_request_id,
        diff_revision=pr.diff_revision,
        repository=pr.repository,
        submitter=pr.submitter,
        summary=pr.summary,
        branch=pr.branch,
        base_commit=pr.base_commit,
        rb_created_at=pr.created_at,
        issue_open_count=pr.issue_open_count,
        ship_it_count=pr.ship_it_count,
        change_reason=change_reason,
        skip_reset=skip_reset,
    )

    if action == 'inserted':
        counts['inserted'] += 1
        logger.debug(f'r/{pr.review_request_id}: inserted as todo')
    elif action == 'updated' and reset:
        counts['updated'] += 1
        logger.info(
            f'r/{pr.review_request_id}: new diff {pr.diff_revision}, reset to todo'
        )
    else:
        counts['skipped'] += 1
```

(Note: the `from collections.abc import Callable` import is no longer needed since `on_progress` is removed.)

- [ ] **Step 4: Run new tests to verify they pass**

Run: `uv run pytest tests/unit/test_queue_sync_progress.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run any existing tests that touch queue_sync**

Run: `uv run pytest tests/unit/ -k "queue_sync or sync_queue" -v`
Expected: PASS (any existing queue_sync tests don't use `on_progress=`, so they still work)

- [ ] **Step 6: Commit**

```bash
git add bb_review/queue_sync.py tests/unit/test_queue_sync_progress.py
git commit -m "refactor(queue_sync): replace on_progress with reporter, add events"
```

---

## Task 7: Wire TUIProgressReporter into the three TUI workers

**Files:**
- Modify: `bb_review/ui/unified_app.py` (`_run_sync` ~line 698, `_run_my_reviews_sync` ~line 765, `_run_fetch_issues` ~line 825)

- [ ] **Step 1: Update _run_sync**

In `bb_review/ui/unified_app.py`, locate `_run_sync` (currently around lines 698-753). Replace the local `on_progress` lambda and the `on_progress=on_progress` argument with a `TUIProgressReporter`.

Find the block (around lines 723-733):

```python
            from bb_review.queue_sync import sync_queue

            def on_progress(current: int, total: int) -> None:
                self.call_from_thread(self._task_start, "sync", f"sync {current}/{total}")

            counts = sync_queue(
                rb_client=rb_client,
                queue_db=self._queue_db,
                days=self._sync_days,
                on_progress=on_progress,
            )
```

Replace with:

```python
            from bb_review.queue_sync import sync_queue
            from bb_review.ui.progress_reporter import TUIProgressReporter

            reporter = TUIProgressReporter(self, task_key='sync', label='sync')

            counts = sync_queue(
                rb_client=rb_client,
                queue_db=self._queue_db,
                days=self._sync_days,
                reporter=reporter,
            )
```

- [ ] **Step 2: Update _run_my_reviews_sync**

In the same file, locate `_run_my_reviews_sync` (currently around lines 765-823). Replace the equivalent block (around lines 794-804):

```python
            from bb_review.queue_sync import sync_queue

            def on_progress(current: int, total: int) -> None:
                self.call_from_thread(self._task_start, "my_reviews_sync", f"my-sync {current}/{total}")

            counts = sync_queue(
                rb_client=rb_client,
                queue_db=self._my_reviews_db,
                submitter=username,
                on_progress=on_progress,
            )
```

Replace with:

```python
            from bb_review.queue_sync import sync_queue
            from bb_review.ui.progress_reporter import TUIProgressReporter

            reporter = TUIProgressReporter(self, task_key='my_reviews_sync', label='my-sync')

            counts = sync_queue(
                rb_client=rb_client,
                queue_db=self._my_reviews_db,
                submitter=username,
                reporter=reporter,
            )
```

- [ ] **Step 3: Update _run_fetch_issues**

In the same file, locate `_run_fetch_issues` (currently around lines 825-879). It currently does not pass any progress callback to `fetch_all_comments`. Pass a `TUIProgressReporter`:

Find:

```python
            fetcher = RBCommentFetcher(rb_client, config.reviewboard.bot_username)
            all_comments = fetcher.fetch_all_comments(rr_id)
```

Replace with:

```python
            from bb_review.ui.progress_reporter import TUIProgressReporter

            fetcher = RBCommentFetcher(rb_client, config.reviewboard.bot_username)
            issues_reporter = TUIProgressReporter(
                self,
                task_key=task_key,
                label=f'issues r/{rr_id}',
            )
            all_comments = fetcher.fetch_all_comments(rr_id, reporter=issues_reporter)
```

(`task_key` is already a local variable: `f"issues-{rr_id}"`.)

- [ ] **Step 4: Verify the TUI module still imports**

Run: `uv run python -c "from bb_review.ui import unified_app; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Run the relevant tests for regressions**

Run: `uv run pytest tests/unit/ -v`
Expected: PASS (TUI isn't unit-tested directly; this just confirms nothing else broke)

- [ ] **Step 6: Commit**

```bash
git add bb_review/ui/unified_app.py
git commit -m "feat(ui): use TUIProgressReporter in sync, my-sync, fetch-issues workers"
```

---

## Task 8: Wire ClickProgressReporter into cli/queue.py

**Files:**
- Modify: `bb_review/cli/queue.py`

- [ ] **Step 1: Update sync command**

In `bb_review/cli/queue.py`, locate the block around lines 67-83 (the `_progress` lambda and `sync_queue(... on_progress=_progress)` call).

Find:

```python
    from ..queue_sync import sync_queue

    click.echo(f"Syncing reviews (last {days} days, limit {limit})...")

    def _progress(current: int, total: int) -> None:
        click.echo(f"\rFetching: {current}/{total}...", nl=False)

    counts = sync_queue(
        rb_client=rb_client,
        queue_db=queue_db,
        days=days,
        limit=limit,
        repository=repository,
        submitter=submitter,
        bot_only=bot_only,
        prune=prune,
        on_progress=_progress,
    )
    click.echo()  # newline after progress
```

Replace with:

```python
    from ..cli._progress import ClickProgressReporter
    from ..queue_sync import sync_queue

    click.echo(f"Syncing reviews (last {days} days, limit {limit})...")

    counts = sync_queue(
        rb_client=rb_client,
        queue_db=queue_db,
        days=days,
        limit=limit,
        repository=repository,
        submitter=submitter,
        bot_only=bot_only,
        prune=prune,
        reporter=ClickProgressReporter(),
    )
```

(The trailing `click.echo()` is no longer needed — `ClickProgressReporter` handles the trailing newline itself on the final tick or first non-tick event.)

- [ ] **Step 2: Verify the CLI imports**

Run: `uv run bb-review queue --help`
Expected: help text printed, no errors

- [ ] **Step 3: Run CLI tests for regressions**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS (no test directly invokes `queue sync`, but a smoke check on CLI wiring catches import errors)

- [ ] **Step 4: Commit**

```bash
git add bb_review/cli/queue.py
git commit -m "refactor(cli/queue): use ClickProgressReporter for sync command"
```

---

## Task 9: Full test pass + manual smoke

- [ ] **Step 1: Run the full test suite**

Run: `task test`
Expected: PASS

- [ ] **Step 2: Run lint**

Run: `task lint`
Expected: PASS (no new warnings introduced)

- [ ] **Step 3: Manual TUI smoke test**

Run: `uv run bb-review interactive`
- Press `R` (or whatever the Refresh keybinding is) on the Queue tab.
- Verify the log panel now shows lines like:
  - `Fetching review requests from RB (last 10 days, max 200)...`
  - `Got N review requests from RB, hydrating...`
  - `Processed X/N` lines (intermittent, ~2s apart)
  - `Processed N/N` (always on completion)
  - `Reconciling N review requests against local queue...`
  - Possibly: `r/<id>: checking diff <a>-><b> for content change...`
  - Possibly: `Pruning items no longer on RB...`
  - Existing final summary line.
- Verify the title bar still shows `sync X/N` updating live.
- Repeat for My Reviews tab refresh, and for opening issues on an RR (fetch-issues).

- [ ] **Step 4: Manual CLI smoke test**

Run: `uv run bb-review queue sync --days 1 --limit 5`
Expected:
- Existing pre-message (`Syncing reviews (last 1 days, limit 5)...`)
- New checkpoint lines from the reporter
- `Fetching: X/N...` counter overwriting in place
- `Reconciling N review requests...` checkpoint on a new line
- Possibly per-RR `item_event` lines
- Existing summary line on a fresh row

- [ ] **Step 5: If everything looks good, no commit needed (only verification work in this task)**

---

## Self-Review Notes

Cross-check against spec sections:

- **Architecture / 3 components:** Task 1 (protocol + null), Task 2 (TUI), Task 3 (CLI) ✓
- **`sync_queue` checkpoints:** "Reconciling..." in Task 6 step 3 ✓, "Pruning..." in Task 6 step 3 ✓
- **`_sync_one` item_event for diffs_equal:** Task 6 step 3 ✓ (test in Task 6 step 1)
- **`get_recent_reviews` / `get_pending_reviews` checkpoints + ticks:** Task 4 ✓
- **`fetch_all_comments` checkpoint + ticks:** Task 5 ✓
- **Caller migration table (5 sites):** Task 7 (3 TUI workers) + Task 8 (cli/queue) + cli/poll (unchanged, default null) ✓
- **Expected log output sample:** matches what Task 6/4/5/7 produce ✓

Type/signature consistency:
- `reporter: ProgressReporter | None = None` parameter is consistent across `sync_queue`, `get_recent_reviews`, `get_pending_reviews`, `fetch_all_comments` ✓
- `TUIProgressReporter` constructor used identically in three TUI workers (positional `app`, keyword `task_key=`, `label=`) ✓
- `task_key='sync'` matches the existing `_task_start("sync", ...)` / `_task_done("sync")` calls in `_run_sync`; likewise `'my_reviews_sync'` and `f'issues-{rr_id}'` ✓

Placeholder scan: no TBDs, every code step contains complete code.
