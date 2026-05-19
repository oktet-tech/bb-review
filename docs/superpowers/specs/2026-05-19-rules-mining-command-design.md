# Rules-Mining Command Design

Date: 2026-05-19

## Goal

Add a CLI feature that mines past human reviewer comments from Review Board
for a repository and drafts a candidate rules file (`draft-rules.md`) for that
repo's review guide. This bootstraps or augments `guides/{repo}/` content from
ground-truth reviewer behavior instead of hand-authoring it.

## Scope

Two-step process exposed as a new `rules` CLI command group:

1. **Fetch** — pull human reviewer comments from recent submitted and
   discarded RRs into a dedicated cache database.
2. **Draft** — run an agent over the cached comments (with repo code access)
   to synthesize a `draft-rules.md` suggestions file.

The split exists so the slow, flaky RB fetch is done once and the synthesis
step can be re-run and iterated for free.

Out of scope for v1:

- Fetching the diff hunk / surrounding code for each comment (comment text +
  file + line is enough; `rb_fetcher` does not pull it today).
- Direct-LLM synthesis backend. Synthesis is agent-only (Claude Code / Codex).
- Writing directly into curated guide files. Output is always a standalone
  non-destructive `draft-rules.md`.

## Command Surface

New CLI module `bb_review/cli/rules.py` registering a `rules` Click group on
`main`, matching the existing `poll` / `queue` / `repos` / `db` group pattern.

- `bb-review rules fetch REPO [--count N] [--days D] [--refresh]`
  Discover the N most recent submitted + discarded RRs for `REPO`, fetch
  human reviewer comments, upsert into the cache DB. Incremental: RRs already
  cached are skipped unless `--refresh` is passed.
- `bb-review rules draft REPO [--method claude|codex] [--transcript PATH]`
  Load cached comments for `REPO`, run an agent, write
  `guides/{repo}/draft-rules.md`.
- `bb-review rules show REPO`
  Report what is cached for `REPO` (RR count, comment count) so the user can
  inspect coverage before drafting.

`REPO` is the repository name as used in config and the `guides/` directory.
It maps to the RB `repository` filter parameter.

## Data Source

Human reviewer comments fetched from Review Board. RR selection: the N most
recent **submitted and discarded** review requests for the repo. Both carry
full review history; discarded RRs additionally capture "this was wrong"
signal. Pending RRs are excluded (half-reviewed, weaker signal).

The bot's own comments are excluded via the existing
`RBCommentFetcher(bot_username=...)` filtering.

## Cache Database

New file `~/.bb_review/rules_mining.db`, separate from `reviews.db` and
`state.db` so it can be deleted and re-fetched freely while iterating. New
module `bb_review/db/mining_db.py` with a `MiningDatabase` class.

### Table `mined_review_requests`

| Column           | Type    | Notes                                  |
|------------------|---------|----------------------------------------|
| `rr_id`          | INTEGER | Primary key                            |
| `repository`     | TEXT    | Indexed                                |
| `rr_status`      | TEXT    | `submitted` or `discarded`             |
| `rr_summary`     | TEXT    |                                        |
| `submitter`      | TEXT    |                                        |
| `branch`         | TEXT    |                                        |
| `rb_last_updated`| TEXT    | RB last-updated; lets `--refresh` detect staleness |
| `fetched_at`     | TEXT    | When this tool cached the RR           |

### Table `mined_comments`

| Column           | Type    | Notes                                  |
|------------------|---------|----------------------------------------|
| `id`             | INTEGER | Primary key, autoincrement             |
| `rr_id`          | INTEGER | FK -> `mined_review_requests`, indexed |
| `review_id`      | INTEGER |                                        |
| `comment_id`     | INTEGER |                                        |
| `reviewer`       | TEXT    |                                        |
| `text`           | TEXT    |                                        |
| `file_path`      | TEXT    | Nullable (body comments)               |
| `line_number`    | INTEGER | Nullable                               |
| `is_body_comment`| INTEGER | Boolean                                |
| `issue_opened`   | INTEGER | Boolean                                |
| `issue_status`   | TEXT    | `open` / `resolved` / `dropped` / null |
| `reply_to_id`    | INTEGER | Nullable                               |

`issue_status` is the key quality signal and must be preserved:

- `resolved` -> submitter agreed and fixed -> confirmed mistake, strong rule
  candidate.
- `dropped` -> pushback / disagreement -> weak signal, false-positive
  candidate.
- plain body comments -> softer guidance.

## Fetch Step

1. RB's `/api/review-requests/` `status` parameter is single-valued, so fetch
   twice (`status=submitted`, `status=discarded`), merge, sort by
   `last_updated` descending, take the top N.
2. Add a lightweight `rb_client` method
   `list_repo_review_requests(repository, statuses, limit, days)` returning RR
   metadata dicts. The existing `get_recent_reviews` is pending-specific and
   builds heavier `PendingReview` objects, so it is not reused.
3. For each RR not already cached (unless `--refresh`):
   `RBCommentFetcher.fetch_all_comments(rr_id)` -> upsert rows into both
   tables.
4. Per-RR fetch errors are logged and skipped; the batch continues. A partial
   cache is acceptable because fetch is incremental.
5. RB authentication failure aborts the fetch via the existing client error.

## Synthesize Step

1. Load all `mined_comments` for `REPO` from the cache DB.
2. Write them into a single input artifact (markdown, grouped by file). Each
   comment is annotated with `issue_status`, `reviewer`, RR id, and RR status,
   so recurrence across RRs is visible to the agent.
3. Ensure the repo checkout exists via `RepoManager` at `main`/HEAD. The agent
   runs with cwd set to the checkout so it can open referenced files.
4. Run the agent. Extract a shared "run agent CLI, return text output" helper
   from the subprocess plumbing in `reviewers/claude_code.py` and
   `reviewers/codex.py` (CLI flags, MCP setup, transcript handling). The
   rules-drafting prompt is bespoke and does not use the reviewers'
   issue-parsing output path.
5. The synthesis prompt instructs the agent to:
   - Cluster recurring human-reviewer mistakes across RRs.
   - Weight `resolved` issues highest; treat `dropped` issues as disagreement
     / false-positive candidates.
   - Prioritize rules observed across multiple RRs.
   - Optionally open referenced files in the checkout to ground each rule.
   - Output markdown matching the `technical-patterns.md` structure, plus a
     "false-positive candidates" section derived from `dropped` issues.
   - If `guides/{repo}/technical-patterns.md` exists, it is passed as context
     so suggestions are new rather than duplicates.
6. Agent output is written to `guides/{repo}/draft-rules.md`, overwriting any
   existing draft (it is a regenerable suggestions file). `--transcript` saves
   the agent transcript, consistent with other commands.

`--method` defaults to `claude`.

## Error Handling

- `rules draft` with zero cached comments for `REPO` -> clear error directing
  the user to run `rules fetch` first.
- Agent failure or empty output -> error; any transcript is preserved.
- RB authentication failure during fetch -> abort with the existing client
  error.

## Testing

- Extend `MockRBClient` (`tests/mocks/`) with submitted/discarded RR listing
  and comment fixtures.
- Unit tests for `MiningDatabase`: upsert, incremental skip of cached RRs,
  query-by-repo.
- CLI tests with Click's `CliRunner`:
  - `rules fetch` against the mock RB client.
  - `rules draft` with a mocked agent runner, mirroring how existing reviewer
    tests mock the agent subprocess.

## Resolved Decisions

- `--method` for `rules draft` defaults to `claude` (fixed default, not
  derived from the per-repo `review_method` config override).
- `rules fetch` re-fetches a cached RR only on explicit `--refresh`. It never
  auto-re-fetches based on `rb_last_updated`; that column is stored for
  inspection and possible future use.
