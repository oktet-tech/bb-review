# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BB Review is a Python CLI tool for AI-powered code reviews on Review Board (RB). It fetches diffs from RB, analyzes them using LLMs (Anthropic, OpenRouter, OpenAI), and posts review comments back. Supports four review backends (direct LLM, OpenCode agent, Claude Code CLI, Codex CLI), interactive TUI with queue management, comment triage with auto-reply, daemon polling, and semantic code search via CocoIndex.

## Commands

### Build & Install
```bash
uv sync --all-extras          # Install all dependencies
```

### Testing
```bash
task test                      # All tests (uv run pytest tests/ -v)
task test:unit                 # Unit tests only (tests/unit/)
task test:integration          # Integration tests only (tests/integration/)
task test:cli                  # CLI tests only (tests/cli/)
task test:cov                  # Tests with coverage report
uv run pytest tests/unit/test_config.py -v          # Single test file
uv run pytest tests/unit/test_config.py::test_name  # Single test function
```

Test markers: `@pytest.mark.slow`, `@pytest.mark.integration`. Deselect with `-m "not slow"`.

### Linting & Formatting
```bash
task lint                      # Check with ruff
task format                    # Auto-fix and format
task check                     # Check without modifying
```

### Running
```bash
uv run bb-review --help
uv run bb-review analyze <review-id> --dry-run       # Direct LLM analysis
uv run bb-review opencode <review-id> --dry-run      # OpenCode agent analysis
uv run bb-review claude <review-id> --dry-run         # Claude Code CLI analysis
uv run bb-review codex <review-id> --dry-run          # Codex CLI analysis
uv run bb-review submit review.json                   # Submit review to RB
uv run bb-review interactive                          # Interactive TUI
uv run bb-review interactive --tab queue              # TUI starting at queue tab
uv run bb-review comments <review-id>                 # Dump RR comments with context
uv run bb-review triage <review-id>                   # Triage comments, plan fixes
uv run bb-review resolve <review-id>                  # Mark comments fixed/dropped on RB
uv run bb-review poll daemon                          # Daemon polling mode
uv run bb-review queue sync                           # Sync pending RRs from RB
uv run bb-review queue process                        # Process queued items
uv run bb-review repos sync                           # Fetch/update repositories
uv run bb-review db list                              # List stored analyses
uv run bb-review transcript _files/t.json             # Pretty-print agent transcript
```

## Architecture

```
Review Board --> bb_review CLI --> LLM API (Anthropic/OpenRouter/OpenAI)
                     |
                     |-- Local Git Repos (clone, checkout, patch)
                     |-- OpenCode Agent (optional, MCP servers)
                     |-- Claude Code CLI (optional, agentic with tools)
                     |-- Codex CLI (optional, OpenAI agentic with sandbox)
                     '-- CocoIndex (optional, semantic search via PostgreSQL+pgvector)
```

### Package Structure (`bb_review/`)

- **`cli/`** -- Click CLI commands. Each file registers on the `main` group. Config lazy-loaded via `get_config(ctx)`.
  - `analyze.py`, `opencode.py`, `claude_code.py`, `codex.py` -- Four review backends (all support `--transcript`)
  - `transcript.py` -- Pretty-print agent transcripts saved by `--transcript`
  - `submit.py` -- Post reviews to RB
  - `interactive.py` -- TUI launcher
  - `comments.py` -- Dump RR comments with source context
  - `triage.py` -- Triage comments, plan fixes
  - `resolve.py` -- Mark comments fixed/dropped
  - `poll.py` -- Polling group (once, daemon, status)
  - `queue.py` -- Queue group (sync, list, set, process, stats, show)
  - `repos.py` -- Repos group (list, sync, init-guidelines, mcp-setup, clean)
  - `db.py` -- DB group (list, show, export, stats, mark, chain, cleanup, search, import)
  - `_review_runner.py` -- Shared orchestration for agent-based reviewers (single/chain/series modes)
  - `_session.py` -- ReviewSession dataclass bundling config, clients, callbacks
  - `utils.py` -- Review ID parsing, logging setup
- **`reviewers/`** -- Analysis engines
  - `llm.py` -- Direct LLM analysis (`Analyzer` class), JSON schema output
  - `opencode.py` -- OpenCode agent subprocess, `###Issue:` structured output parsing
  - `claude_code.py` -- Claude Code CLI subprocess, JSON envelope unwrapping, MCP support
  - `codex.py` -- Codex CLI subprocess (`codex exec`), plain text output via `-o`
  - `providers.py` -- LLM provider factory (`AnthropicProvider`, `OpenRouterProvider`, `OpenAIProvider`)
  - `diff_utils.py` -- Diff hunk extraction for inline context
- **`rr/`** -- Review Board integration
  - `rb_client.py` -- API client using `curl` subprocess for Kerberos auth
  - `rb_commenter.py` -- Comment formatting and posting, deduplication of previously-dropped comments
  - `rb_fetcher.py` -- Fetch all comments from RB reviews (for triage)
  - `chain.py` -- Patch series dependency resolution (walks `depends_on` links)
  - `dedup.py` -- Fuzzy-match new comments against dropped ones (60% SequenceMatcher threshold)
- **`triage/`** -- Comment triage and fix planning
  - `analyzer.py` -- LLM classifies comments (valid/confused/nitpick/outdated/already_fixed/duplicate)
  - `models.py` -- TriagedComment, TriageResult, FixPlan, SelectableTriagedComment
  - `plan_writer.py` -- YAML persistence for fix plans
  - `replier.py` -- Auto-posts replies to RB (groups by review, publishes atomically)
  - `agent_triage.py` -- Claude/OpenCode CLI runners for triage
- **`db/`** -- SQLite databases
  - `review_db.py` -- Analyses, comments, chains, triage sessions (44KB, feature-rich)
  - `queue_db.py` -- Queue state machine with validated transitions (todo/next/ignore/in_progress/done/failed)
  - `models.py` -- StoredAnalysis, StoredComment, StoredChain
  - `queue_models.py` -- QueueItem, state transitions
  - `export.py` -- JSON/Markdown export utilities
- **`git/manager.py`** -- RepoManager: clone, fetch, checkout, patch. Context managers `checkout_context()` and `chain_context()` for atomic operations.
- **`indexing/`** -- CocoIndex semantic search: `indexer.py` (sentence-transformers embeddings, PostgreSQL+pgvector), `mcp.py` (FastMCP server for OpenCode/Claude integration)
- **`ui/`** -- Textual TUI
  - `unified_app.py` -- Main app with 4 tabs (Queue, Reviews, My Reviews, Work)
  - `review_handler.py` -- Review actions: export, submit, delete, status updates
  - `triage_handler.py` -- Triage actions: open, export, delete
  - `screens/` -- ActionPicker, CommentPicker (3-state toggle with diff view), FilterPicker, IssuesScreen, TriageScreen (FIX/REPLY/SKIP/DISAGREE), TriageViewScreen, export screens
  - `widgets/` -- QueuePane, ReviewsPane, MyReviewsPane, WorkPane, DiffViewer, LogPanel
  - `models.py` -- CommentStatus, SelectableComment, ExportableAnalysis
- **`config.py`** -- Pydantic models: Config, ReviewBoardConfig, LLMConfig, OpenCodeConfig, ClaudeCodeConfig, CodexConfig, CocoIndexConfig, QueueConfig, ReviewDBConfig. Env var `${VAR}` resolution. Per-repo `review_method` override.
- **`models.py`** -- Core data models: ReviewResult, ReviewComment, ChainReviewResult, ReviewGuidelines, RepoConfig, Severity (LOW/MEDIUM/HIGH/CRITICAL), ReviewFocus
- **`guidelines.py`** -- Load `.ai-review.yaml` + rich guide dirs (`guides/{repo}/`). Subsystem trigger matching against diff files. `load_rich_context()` concatenates technical-patterns.md, false-positive-guide.md, matched subsystem guides.
- **`guidelines_deploy.py`** -- Deploy skills/commands from `guides/{repo}/` into repo checkout's agent config dir (`.claude/commands/`, `.codex/`, `.opencode/`) before agent launch.
- **`poller.py`** -- Polling daemon with SQLite state tracking, deduplication on (rr_id, diff_revision)

### Review Processing Flow

1. Fetch diff and review request metadata from Review Board
2. Resolve dependency chain (walks `depends_on` links, validates no diamonds/cycles)
3. Checkout base commit in local repo cache, apply patches in order
4. Load per-repo guidelines from `.ai-review.yaml`
5. Analyze via direct LLM, OpenCode agent, Claude Code CLI, or Codex CLI
6. Deduplicate against previously-dropped comments (fuzzy matching)
7. Optionally edit in interactive TUI (CommentPicker with 3-state toggles)
8. Save to reviews database
9. Post comments back to Review Board

### Three Review Modes

1. **Single** -- One RR analyzed in isolation
2. **Chain** -- Multiple dependent RRs analyzed sequentially, each with cumulative patch context
3. **Series** (`--series` flag) -- Entire patch set analyzed as one unit, issues partitioned across RRs

### Authentication

RB client uses `curl` subprocess (not Python `requests`) because Kerberos auth through Apache requires negotiate headers that `requests-kerberos` doesn't handle reliably. Supports: API token, Kerberos, username/password, and Kerberos+password (two-layer). Session cookies stored in temp files.

## Code Style

- **Line length**: 110 characters
- **Python**: 3.10+ (uses `X | Y` union syntax)
- **Quotes**: Single quotes everywhere (inline, docstrings, multiline)
- **Linter**: Ruff with rules E, F, W, I, UP, B
- **Imports**: Force-sorted within sections, 2 blank lines after imports
- **Max complexity**: mccabe 16, max-args 8, max-branches 30

## Commit Convention

Semantic commit messages: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`. First line under 72 characters.

## Known Gotchas

- No emojis in review text posted to RB -- the server returns 500
- RB API returns dicts, not objects -- use `.get()` not `.attribute`
- Use `--data-urlencode` for curl POST data, not `-d`
- OpenCode: don't use `--agent plan` for review -- it doesn't output to stdout
- MCP server logging must use stderr; stdout is reserved for JSON-RPC protocol
- FastMCP must use `show_banner=False` or it breaks MCP protocol
- Chain resolution stops at submitted RRs (uses them as base commits)
- QueueDatabase and ReviewDatabase share `reviews.db` file but initialize independently
- Queue state machine validates transitions explicitly -- see `VALID_TRANSITIONS` in `queue_db.py`
- Claude Code reviewer unwraps JSON envelope `{"type":"result","subtype":"...","result":"..."}`
- OpenCode reviewer parses two output formats: `### Issue:` (preferred) and `**N. Title**` (fallback)

## Configuration

- Main config: `./config.yaml` or `~/.bb_review/config.yaml` (contains secrets, git-ignored)
- Template: `config.example.yaml`
- Per-repo review guidelines: `guides/{repo}.ai-review.yaml` and `.ai-review.yaml` in repo root
- Rich per-repo guides: `guides/{repo}/` dirs with skills, commands, technical patterns, subsystems (see `guides/README.md`)
- Per-repo review method override: `review_method: llm|opencode|claude|codex` in repository config
- State DB: `~/.bb_review/state.db` (polling)
- Reviews DB: `~/.bb_review/reviews.db` (analyses, queue, triage)
- Logs: `~/.bb_review/bb_review.log`

## Testing Notes

Tests use mock providers (`tests/mocks/`) -- `MockLLMProvider` and `MockRBClient` -- so no external API calls are needed. Test data fixtures live in `tests/data/`. CLI tests use Click's `CliRunner` with isolated filesystems. 26 unit test files, 3 integration, 8 CLI test files.
