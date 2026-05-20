# Rules-Mining: Cache Diff Hunks With Comments

Date: 2026-05-20

## Goal

Extend the rules-mining cache so each diff comment can carry the unified
diff hunk it was made on, giving the synthesis agent the actual code change
the reviewer was looking at instead of only `file_path:line_number`. Better
grounding for extracted rules without re-fetching the comment itself.

## Scope

A new opt-in CLI flag on the existing `rules fetch` command:

```
bb-review rules fetch <repo> --with-diff-hunks [...]
```

When the flag is set, the fetcher pulls the per-comment diff hunk from
Review Board for each diff comment and stores it in a new `diff_hunk`
column on `mined_comments`. The flag also acts as a backfill switch: if the
RR is already cached, comments with `NULL` hunks get their hunks filled in
without re-fetching the comments themselves.

The flag is **off by default** so existing fetch behavior is unchanged.

Out of scope:

- Fetching hunks for body comments (no file/line).
- Backfilling automatically on every fetch (must be opted in).
- A separate `rules fetch-hunks` subcommand. The flag covers the backfill
  case sufficiently.
- Versioning of stored hunks across diff revisions for the same comment.
  A comment is on exactly one diff revision; we store the hunk for that
  revision.

## Building Blocks Reused

- `bb_review/reviewers/diff_utils.py::extract_diff_hunk(raw_diff, file_path,
  line_number)` — already returns the unified diff hunk whose new-file line
  range contains the given line. No changes needed.
- `bb_review/rr/rb_client.py::ReviewBoardClient.get_diff(rr_id, revision)`
  — already returns `DiffInfo` with `raw_diff` (full unified diff string).

## Schema Change

Add two nullable columns to `mined_comments`: `diff_revision INTEGER` and
`diff_hunk TEXT`. Both are needed: the hunk is the content, the revision
is what lets the backfill path know which diff to fetch for a given
comment. The existing `MiningDatabase._ensure_db` performs a lightweight
migration matching the pattern in `bb_review/db/queue_db.py`:

```python
cols = {row[1] for row in conn.execute("PRAGMA table_info(mined_comments)").fetchall()}
if "diff_revision" not in cols:
    conn.execute("ALTER TABLE mined_comments ADD COLUMN diff_revision INTEGER")
if "diff_hunk" not in cols:
    conn.execute("ALTER TABLE mined_comments ADD COLUMN diff_hunk TEXT")
```

Existing rows keep `NULL` for both. No re-fetch needed unless
`--with-diff-hunks` is used.

`MinedComment` (dataclass) gains `diff_revision: int | None = None` and
`diff_hunk: str | None = None`. `RBComment` in `bb_review/triage/models.py`
gains the same two fields. These are additive, non-breaking enrichments —
the triage flow ignores both.

`record_review_request` keeps its current signature (`list[RBComment]`);
the new fields on `RBComment` flow straight into the INSERT.

Two new `MiningDatabase` methods, both keyed by RB's natural identifiers
(no internal row id exposed):

- `get_comments_missing_hunks(rr_id) -> list[MinedComment]` — diff comments
  for `rr_id` where `diff_hunk IS NULL AND file_path IS NOT NULL`. Each
  returned `MinedComment` carries its `diff_revision` so the backfill path
  can fetch the right diff without re-querying RB.
- `update_comment_diff_hunk(rr_id, comment_id, hunk)` — `UPDATE
  mined_comments SET diff_hunk = ? WHERE rr_id = ? AND comment_id = ? AND
  file_path IS NOT NULL`.

## Architecture

The diff-hunk extraction is rules-specific augmentation, **not** part of
`RBCommentFetcher`. The fetcher stays generic. The augmentation runs in
`bb_review/rules/fetcher.py`:

```
list_repo_review_requests  ->  per RR:
    RBCommentFetcher.fetch_all_comments  ->  list[RBComment]
    if with_diff_hunks:
        diffs_cache[(rr_id, rev)] = rb_client.get_diff(rr_id, rev).raw_diff
        for each comment with file_path/line_number:
            extract_diff_hunk(diffs_cache[...], comment.file_path, comment.line_number)
    mining_db.record_review_request(..., comments=[(RBComment, hunk), ...])
```

`fetch_repo_rules_data` gains `with_diff_hunks: bool = False` and a new
counts key `hunks_backfilled: int`.

### Diff revision discovery

Each diff comment dict from RB has a `links.filediff.href` like
`.../review-requests/{rr_id}/diffs/{rev}/files/{fid}/`. We extract `{rev}`
with a small regex helper `_extract_diff_revision(comment_dict) -> int |
None`. Today, `RBCommentFetcher._resolve_file_path` already runs a similar
regex on the same href; we just need to expose the revision in addition to
the filediff id.

Cleanest plumbing: add `diff_revision: int | None` to `RBComment` (it
naturally belongs there — it locates the comment within the RR) and have
`RBCommentFetcher` populate it from the same href it already parses.

### Per-RR diff memoization

A single dict scoped to one `fetch_repo_rules_data` call:

```python
diffs_cache: dict[tuple[int, int], str] = {}
def _get_diff(rr_id: int, rev: int) -> str:
    key = (rr_id, rev)
    if key not in diffs_cache:
        diffs_cache[key] = rb_client.get_diff(rr_id, rev).raw_diff
    return diffs_cache[key]
```

In practice an RR has 1 diff revision most of the time, so this is one
extra RB call per cached RR.

## Backfill semantics

When an RR is already cached:

| Case | `--with-diff-hunks` off | `--with-diff-hunks` on |
|------|-------------------------|------------------------|
| RR cached, hunks already present | skip (today's behavior) | skip |
| RR cached, hunks `NULL` | skip | **backfill** |
| RR not cached | full fetch (no hunks) | full fetch (with hunks) |
| `--refresh` set | re-fetch (no hunks) | re-fetch (with hunks) |

The backfill path:

1. `mining_db.get_comments_missing_hunks(rr_id)` returns the rows.
2. For each, compute the hunk via the memoized `_get_diff(rr_id, rev)` and
   `extract_diff_hunk`.
3. `mining_db.update_comment_diff_hunk(row_id, hunk)` for non-null hunks.
4. Increment `hunks_backfilled` once per RR where at least one comment got
   a new hunk; the RR is **not** added to the `fetched` counter.

## Artifact Formatting Change

`format_comments_artifact` in `bb_review/rules/synthesizer.py` renders a
fenced `diff` code block immediately after a comment whose `diff_hunk` is
non-empty. Body comments and `NULL`-hunk comments fall back to today's
output unchanged.

Example shape:

```
- [RR #123 | submitted | reviewer: alice | resolved] src/foo.c:42
  check the return value
  ```diff
  @@ -38,7 +38,9 @@ static int frob(...)
       int rc;
  -    do_thing();
  +    rc = do_thing();
  +    if (rc < 0)
  +        return rc;
       return 0;
  ```
```

`build_rules_prompt` gets one additional sentence noting that diff hunks
may appear under each comment and that the agent should use them (when
present) as the ground-truth code the reviewer was looking at.

## CLI Surface

One new option on `bb-review rules fetch`:

```
--with-diff-hunks    Also fetch and cache the diff hunk for each diff
                     comment. Backfills missing hunks on already-cached RRs.
                     Default: off.
```

`rules show` and `rules draft` need no CLI changes. The draft path picks up
the hunks automatically through `format_comments_artifact`.

## Error Handling

- Filediff href without a parsable diff revision → `comment.diff_revision =
  None`, hunk stays `NULL`. Debug log.
- `rb_client.get_diff(rr_id, rev)` raises → log warning with `(rr_id,
  rev)`, mark every comment on that `(rr_id, rev)` as `NULL` hunk for this
  run, continue the batch. One bad diff must not abort a 60-RR fetch.
- `extract_diff_hunk` returns `None` (hunk not found — comment can land on
  a context line, or the path encoding differs) → `NULL`. No retry. Debug
  log.

## Testing

### Unit

- `tests/unit/test_mining_db.py`
  - `diff_hunk` round-trip through `record_review_request` /
    `get_comments_for_repo`.
  - `update_comment_diff_hunk` updates the column.
  - `get_comments_missing_hunks` returns only diff comments where
    `diff_hunk IS NULL AND file_path IS NOT NULL`.
  - Migration: open a DB created before the column existed, re-open with
    the current `MiningDatabase`, confirm the `diff_hunk` column was added
    and existing rows are intact.

- `tests/unit/test_rules_fetcher.py`
  - `--with-diff-hunks` (i.e. `with_diff_hunks=True`) populates `diff_hunk`
    for diff comments and leaves it `NULL` for body comments.
  - Each `(rr_id, diff_revision)` is fetched exactly once even with many
    comments on the same filediff.
  - Backfill: an RR already cached with `NULL` hunks gets its hunks filled
    in without re-recording the RR; `counts["hunks_backfilled"] == 1` and
    `counts["fetched"] == 0`.
  - `rb_client.get_diff` failure → all comments on that `(rr_id, rev)`
    end up with `NULL` hunk and the batch continues.

- `tests/unit/test_rules_synthesizer.py`
  - A comment with `diff_hunk` set renders a fenced ` ```diff ` block in
    the artifact.
  - A comment with `diff_hunk = None` renders today's output verbatim
    (regression guard).

### CLI

- `tests/cli/test_rules.py`
  - `rules fetch --with-diff-hunks` forwards `with_diff_hunks=True` to the
    monkeypatched `fetch_repo_rules_data`.

### Mocks

`tests/mocks/rb_client.py` — extend `MockRBClient` with a `diffs` mapping
keyed by `(rr_id, revision)` returning `MockDiffInfo(raw_diff=...)` from
`get_diff()`. The existing `get_diff` mock currently only keys by `rr_id`;
extend it to optionally key by `(rr_id, revision)` while remaining
backward-compatible with the old shape.

## Resolved Decisions

- `--with-diff-hunks` is **off by default**; users opt in.
- Backfill is folded into the same flag, not a separate subcommand.
- The hunk is the natural unified-diff hunk containing the comment line —
  no custom context windowing.
- Augmentation lives in `bb_review/rules/fetcher.py`, not in
  `RBCommentFetcher`. `RBCommentFetcher` only gains `diff_revision` on
  `RBComment` (parsed from the same filediff href it already touches).
- Hunks flow through the existing `record_review_request(list[RBComment])`
  signature by way of two new optional fields on `RBComment`
  (`diff_hunk`, `diff_revision`). No new parallel arguments, no `(comment,
  hunk)` tuple shape, no internal-id leakage.
- Backfill updates are keyed by `(rr_id, comment_id)`, RB's natural
  identifier pair, not the SQLite row id.
