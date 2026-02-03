# Design Decisions

## Review Queue (Feb 2026)

### QueueDatabase shares DB file with ReviewDatabase

Both classes open the same `reviews.db` file and manage their own tables via `CREATE TABLE IF NOT EXISTS`. This avoids the need to pass both DB instances around and allows cross-querying (e.g., checking the `analyses` table for non-fake entries during sync).

### No FK constraint on analysis_id

The `review_queue.analysis_id` column does NOT use `REFERENCES analyses(id)` because QueueDatabase and ReviewDatabase initialize independently -- if QueueDatabase is created first, the `analyses` table may not yet exist. The column is a logical reference, not enforced by the DB.

### UNIQUE(review_request_id) -- one entry per RR

The queue tracks one entry per review request, not per diff revision. When a new diff is uploaded, the existing entry is updated in-place and reset to `todo`. This keeps the queue concise and avoids duplicate triage work.

### State machine with VALID_TRANSITIONS dict

All status transitions are validated against an explicit dict in `queue_models.py`. This makes the valid paths clear and prevents invalid state changes (e.g., jumping from `todo` directly to `done`).

### Sync skips already-analyzed RRs

During sync, if a queue item exists with the same diff_revision AND a non-fake analysis exists in the `analyses` table, the item is skipped (metadata still updated). This prevents re-queuing work that's already done.

### No-submit default for queue process

`queue process` does NOT submit reviews to Review Board by default. The user reviews results first and submits manually (or uses `--submit`). This prevents accidental auto-posting of low-quality reviews.

### Crash recovery via reset_stale_in_progress

Before picking new work, `queue process` resets any items stuck in `in_progress` back to `next`. This handles the case where a previous run crashed mid-analysis.

### analysis_id retrieved after save (not returned from analyze)

Rather than modifying the existing `process_review()`/`run_analysis()` flow to return a DB ID, the queue process queries `review_db.get_analysis_by_rr()` after the analysis is saved. This keeps the analyze code untouched.

## Queue TUI Screen (Feb 2026)

### QueueListScreen owns the DB handle directly

The screen performs mutations (status changes, deletes) without dismissing itself. This avoids the dismiss/re-push cycle that ExportApp uses and makes shortcuts feel instant. The screen calls `app.refresh_items()` to re-query, then repopulates the table in-place.

### Shortcuts act on selection or cursor

Keyboard shortcuts (n/i/f/d) apply to all selected items if a selection exists, otherwise to the current cursor item. This matches common TUI conventions (e.g., mutt) and avoids requiring explicit selection for single-item operations.

### Action picker modal reuses the same callback pattern

QueueActionPickerScreen dismisses with `(action_key, rr_ids)` tuples. The list screen handles the result in `_on_action_picked` without going through the app. This keeps the flow contained within the screen since QueueApp is intentionally minimal.

### --queue flag on interactive, not a separate command

Queue triage is launched via `bb-review interactive --queue` rather than a separate top-level command. This keeps the CLI surface small and groups all TUI modes under one command. The `--queue-status` option filters queue items (separate from `--status` which filters analyses).

### Prune only removes prunable statuses

`sync --prune` only deletes items with status todo/next/ignore. Items that are in_progress, done, or failed are kept because they represent active or completed work that should remain visible regardless of RB state.

### delete_item is a hard delete, not a soft status

Queue items are fully removed from the DB rather than marked with a "deleted" status. The queue is a working list, not an audit log -- removed items have no value. This keeps the table clean and list queries fast.
