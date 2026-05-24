# Interactive Refresh Progress Logging — Design

## Problem

In the interactive TUI, the Refresh action (and the other refresh-style
background workers) emit only two log lines while doing real work:

```
Starting sync...
Connected to Review Board
(long silence)
Sync complete: 80 fetched, 2 new, 1 reset, 77 unchanged
```

That gap can span several seconds — the Review Board fetch is per-RR and a
single new diff revision triggers an extra `diffs_equal` API call — and during
that gap the log panel looks frozen. The title bar already shows
`sync N/total`, but the log panel itself carries no sense of progress.

Users should see the log panel breathe: a few phase checkpoints, a periodic
counter, and a one-liner whenever a single RR triggers an extra API call.

## Scope

All refresh-style background workers in `bb_review/ui/unified_app.py`:

- `_run_sync` (Queue tab refresh)
- `_run_my_reviews_sync` (My Reviews tab refresh)
- `_run_fetch_issues` (per-RR issues fetch)

Plus the CLI callers that share the underlying functions, so they don't
regress when the `on_progress` parameter is replaced:

- `bb_review/cli/queue.py` (`sync_queue`)
- `bb_review/cli/poll.py` (`get_pending_reviews` — passes nothing today)

Out of scope:

- The `bb_review/rules/` subsystem has its own `on_progress` with a different
  signature; it is not touched.
- Existing `logger.info` / `logger.debug` calls in `queue_sync.py` and
  `rb_client.py` stay as-is; the reporter is a TUI/CLI presentation surface,
  not a replacement for the logfile.

## Architecture

Three components.

### 1. `bb_review/progress.py` (new)

A small `Protocol` with three methods, plus a no-op default. Lives at package
root because both `queue_sync` and `rr/` will import it, and a no-op default
gives CLI callers a zero-friction option.

```python
from typing import Protocol


class ProgressReporter(Protocol):
    def checkpoint(self, msg: str) -> None: ...
    def tick(self, current: int, total: int) -> None: ...
    def item_event(self, msg: str) -> None: ...


class NullProgressReporter:
    def checkpoint(self, msg: str) -> None: pass
    def tick(self, current: int, total: int) -> None: pass
    def item_event(self, msg: str) -> None: pass
```

Method semantics — interpreted by each reporter implementation:

- `checkpoint(msg)`: a phase transition. Always surfaced immediately.
- `tick(current, total)`: per-item progress through a loop. Cadence is the
  reporter's choice (the TUI throttles to ~2s; CLI uses an inline overwrite).
- `item_event(msg)`: a notable thing happened to one item. Always surfaced
  immediately.

### 2. `bb_review/ui/progress_reporter.py` (new)

`TUIProgressReporter` — the implementation used by all three TUI workers.

Construction: `TUIProgressReporter(app, task_key: str, label: str,
min_tick_interval: float = 2.0, clock: Callable[[], float] = time.monotonic)`.

- `app`: the `UnifiedApp` instance; reporter calls `app.call_from_thread(...)`
  to dispatch into the UI thread.
- `task_key`, `label`: identifies the task in the log panel title bar. The
  reporter owns the same `_task_start(key, f"{label} {c}/{t}")` update the
  existing `on_progress` lambdas do today.
- `min_tick_interval`: throttle window for tick lines in the log panel.
  Default 2 seconds.
- `clock`: injectable for tests.

Method behavior:

```
checkpoint(msg):
    call_from_thread(app._log, msg)

item_event(msg):
    call_from_thread(app._log, msg)

tick(current, total):
    call_from_thread(app._task_start, task_key, f"{label} {current}/{total}")
    now = clock()
    if current == total or (now - last_tick_at) >= min_tick_interval:
        call_from_thread(app._log, f"Processed {current}/{total}")
        last_tick_at = now
```

- Title bar updates every tick — no regression from current behavior.
- `last_tick_at` is initialized to `-min_tick_interval` so the very first
  tick is always logged.
- Log panel then gets at most one tick line per `min_tick_interval`, and
  always the final tick (`current == total`).

### 3. `bb_review/cli/_progress.py` (new)

`ClickProgressReporter` — preserves the CLI's existing single-line counter UX
in `cli/queue.py` (which currently does `click.echo(f"\rFetching:
{current}/{total}...", nl=False)`) and gives it checkpoint lines for free.

- `tick(c, t)` → `click.echo(f"\rFetching: {c}/{t}...", nl=False)`,
  unthrottled. The CLI doesn't need throttling — overwriting the same line
  with `\r` is essentially free, and a stable counter feels better in a
  terminal than a 2s-throttled jump.
- `checkpoint(msg)` / `item_event(msg)` → emit a trailing `\n` first if the
  previous output was a tick (so the counter line stays on its own row),
  then `click.echo(msg)`.
- On the final tick, also emit the trailing `\n` so subsequent caller output
  (the summary line in `cli/queue.py`) starts on a fresh row.

Internal state: a single `last_was_tick: bool` flag is enough.

`cli/poll.py` doesn't pass a reporter today; after the change it still
passes nothing and gets the `NullProgressReporter` default.

## Modified functions

Each gains `reporter: ProgressReporter | None = None` (treated as
`NullProgressReporter()` when `None`). The existing `on_progress` parameter
is removed (no shim — `setup.py` says don't add backwards-compat hacks for
internal code).

### `bb_review/queue_sync.py::sync_queue`

Emit points:

- (delegated to `get_recent_reviews` / `get_pending_reviews` — see below)
- After the fetch returns: `reporter.checkpoint(f"Reconciling {N} review
  requests against local queue...")` — only when `N > 0`.
- Before pruning: `reporter.checkpoint("Pruning items no longer on RB...")` —
  only when `prune=True` and there's something to scan.

Pass `reporter` through to `get_recent_reviews` / `get_pending_reviews`.

### `bb_review/queue_sync.py::_sync_one`

Currently the slow per-RR step is the `rb_client.diffs_equal(...)` call when
`change_reason == "new_diff"`. Just before that call:

```python
reporter.item_event(
    f"r/{pr.review_request_id}: checking diff "
    f"{existing.diff_revision}→{pr.diff_revision} for content change..."
)
```

`_sync_one` takes a new `reporter` parameter; `sync_queue` passes it down.

### `bb_review/rr/rb_client.py::get_recent_reviews`

- Before `self._api_get(...)`:
  `reporter.checkpoint(f"Fetching review requests from RB (last {days} days,
  max {limit})...")`
- After the response is parsed:
  `reporter.checkpoint(f"Got {total} review requests from RB, hydrating...")`
- Inside the loop: `reporter.tick(i + 1, total)` after each
  `_to_pending_review` (replaces the existing `on_progress` call).

### `bb_review/rr/rb_client.py::get_pending_reviews`

Same shape with a different checkpoint message
(`"Fetching pending reviews assigned to bot..."`).

### `bb_review/rr/rb_fetcher.py::RBCommentFetcher.fetch_all_comments`

- Before fetching: `reporter.checkpoint(f"Fetching comments for r/{rr_id}...")`
- After each review's comments come back: `reporter.tick(i + 1, total)`.

## Caller migration

| Caller | Today | After |
|---|---|---|
| `unified_app._run_sync` | `on_progress` lambda updating title bar | `TUIProgressReporter(self, "sync", "sync")` passed as `reporter=` |
| `unified_app._run_my_reviews_sync` | same | `TUIProgressReporter(self, "my_reviews_sync", "my-sync")` |
| `unified_app._run_fetch_issues` | no progress | `TUIProgressReporter(self, f"issues-{rr_id}", f"issues r/{rr_id}")`, passed to `fetch_all_comments` |
| `cli/queue.py` | local `_progress` dots | `ClickProgressReporter()` |
| `cli/poll.py` | passes nothing | passes nothing (defaults to null) |
| `cli/comments.py`, `cli/triage.py`, `cli/resolve.py` | call `fetch_all_comments` without progress | unchanged (default null reporter) |

## Expected log output

For a Queue sync of 80 RRs where 2 had new diff revisions, with the per-RR
hydration taking ~5 seconds total:

```
Starting sync...
Connected to Review Board
Fetching review requests from RB (last 10 days, max 200)...
Got 80 review requests from RB, hydrating...
Processed 1/80
Processed 36/80
Processed 71/80
Processed 80/80
Reconciling 80 review requests against local queue...
r/12345: checking diff 3→4 for content change...
r/12410: checking diff 1→2 for content change...
Pruning items no longer on RB...
Sync complete: 80 fetched, 2 new, 1 reset, 77 unchanged
```

Quiet when fast; breathes during the slow phase; any per-RR pause is
explained by a one-liner.

## Error handling

- Reporter methods must not raise. Implementations are simple enough to be
  trusted here; no try/except around reporter calls in the sync code.
- `reporter=None` is treated as `NullProgressReporter()` at the top of each
  modified function. This keeps the call sites tidy (no `if reporter:`
  guards around every emit).

## Testing

Three new unit test files:

### `tests/unit/test_progress.py`

`TUIProgressReporter` in isolation, with a fake clock and a recording sink
(replace `_log` / `_task_start` with list-appending callables; `app` becomes
a `SimpleNamespace`).

- Throttle: `tick(1, 100)` at t=0 logs; `tick(2, 100)` at t=0.5 does not log
  but does update title; `tick(3, 100)` at t=2.1 logs again.
- Final-tick rule: `tick(100, 100)` always logs, even if called immediately
  after a previous tick.
- `checkpoint` and `item_event` always log immediately, regardless of
  throttle state.
- Title bar update fires on every tick.

### `tests/unit/test_queue_sync_progress.py`

`sync_queue` calls the reporter at the right moments. Uses a
`RecordingReporter` test double (a list of `("checkpoint", msg)` /
`("tick", c, t)` / `("item", msg)` tuples).

- 5 RRs, all unchanged: expect the two `get_recent_reviews` checkpoints, N
  ticks, the "Reconciling..." checkpoint, no item events, and (when prune is
  on) the "Pruning..." checkpoint.
- 5 RRs, one with a new diff revision: expect exactly one `item_event`
  matching `r/<id>: checking diff <a>→<b>`.

`MockRBClient` gains a small extension (or fixture parameter) so tests can
declare which RRs report a bumped `diff_revision`.

### `tests/unit/test_rb_client_progress.py`

Verify `get_recent_reviews` emits the pre-call checkpoint, the post-call
"Got N..." checkpoint, and ticks once per RR. Reuses the existing curl-mock
fixtures.

Manual smoke test: run `uv run bb-review interactive`, hit Refresh on the
Queue tab, watch the log panel.

## Implementation order

1. Add `bb_review/progress.py` (protocol + null reporter).
2. Add `bb_review/ui/progress_reporter.py` (`TUIProgressReporter`) and its
   unit tests.
3. Add `bb_review/cli/_progress.py` (`ClickProgressReporter`).
4. Thread `reporter` through `rb_client.get_recent_reviews` /
   `get_pending_reviews` and `rb_fetcher.fetch_all_comments`; add their tests.
5. Thread `reporter` through `sync_queue` and `_sync_one`; add its tests.
6. Update the three TUI workers and the two CLI callers.
7. Manual smoke test.
