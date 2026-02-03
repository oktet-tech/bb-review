# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BB Review is a Python CLI tool that provides AI-powered code reviews for Review Board (RB). It fetches diffs from RB, analyzes them using LLMs (Anthropic, OpenRouter, OpenAI), and posts review comments back. It supports direct LLM analysis, OpenCode agent-based analysis, interactive TUI editing, daemon polling, and semantic code search via CocoIndex.

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
uv run bb-review analyze <review-id> --dry-run     # Direct LLM analysis
uv run bb-review opencode <review-id> --dry-run -O # OpenCode agent analysis
uv run bb-review submit review.json                # Submit review to RB
uv run bb-review interactive                       # Interactive TUI
uv run bb-review poll daemon                       # Daemon polling mode
```

## Architecture

```
Review Board ──> bb_review CLI ──> LLM API (Anthropic/OpenRouter/OpenAI)
                     │
                     ├──> Local Git Repos (clone, checkout, patch)
                     ├──> OpenCode Agent (optional, with MCP servers)
                     └──> CocoIndex (optional, semantic search via PostgreSQL+pgvector)
```

### Key Package Structure (`bb_review/`)

- **`cli/`** — Click CLI commands. Each file registers a subcommand on the `main` group. Config is lazy-loaded via `get_config(ctx)`.
- **`reviewers/`** — Analysis engines: `llm.py` (direct LLM), `opencode.py` (OpenCode agent), `providers.py` (LLM provider factory with `AnthropicProvider`, `OpenRouterProvider`, `OpenAIProvider`).
- **`rr/`** — Review Board integration: `rb_client.py` (API client using `curl` subprocess for Kerberos auth), `rb_commenter.py` (comment formatting/posting), `chain.py` (patch series dependency resolution).
- **`db/`** — SQLite-backed reviews history database: `review_db.py`, `models.py`, `export.py`.
- **`git/manager.py`** — Git repository operations (clone, fetch, checkout, patch application).
- **`indexing/`** — CocoIndex semantic search: `indexer.py` (embeddings), `mcp.py` (MCP server).
- **`ui/`** — Textual-based interactive TUI: `export_app.py`, screens, widgets.
- **`config.py`** — Pydantic-based config loading with env var resolution (`${VAR_NAME}` syntax).
- **`models.py`** — Core data models: `ReviewResult`, `ReviewComment`, `ChainReviewResult`.

### Review Processing Flow

1. Fetch diff and review request metadata from Review Board
2. Checkout base commit in local repo cache
3. Load per-repo guidelines from `.ai-review.yaml` (if present)
4. Analyze via direct LLM or OpenCode agent
5. Optionally edit in interactive TUI
6. Post comments back to Review Board

### Authentication

RB client uses `curl` subprocess (not Python `requests`) because Kerberos auth through Apache requires negotiate headers that `requests-kerberos` doesn't handle reliably. Session cookies are stored in temp files for form-based login.

## Code Style

- **Line length**: 110 characters
- **Python**: 3.10+ (uses `X | Y` union syntax)
- **Quotes**: Single quotes everywhere (inline, docstrings, multiline)
- **Linter**: Ruff with rules E, F, W, I, UP, B
- **Imports**: Force-sorted within sections, 2 blank lines after imports

## Commit Convention

Semantic commit messages: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`. First line under 72 characters.

## Known Gotchas

- No emojis in review text posted to RB — the server returns 500
- RB API returns dicts, not objects — use `.get()` not `.attribute`
- Use `--data-urlencode` for curl POST data, not `-d`
- OpenCode: don't use `--agent plan` for review — it doesn't output to stdout
- MCP server logging must use stderr; stdout is reserved for JSON-RPC protocol
- FastMCP must use `show_banner=False` or it breaks MCP protocol

## Configuration

- Main config: `./config.yaml` or `~/.bb_review/config.yaml` (contains secrets, git-ignored)
- Template: `config.example.yaml`
- Per-repo review guidelines: `guides/{repo}.ai-review.yaml` and `.ai-review.yaml` in repo root
- State DB: `~/.bb_review/state.db`
- Reviews DB: `~/.bb_review/reviews.db`
- Logs: `~/.bb_review/bb_review.log`

## Testing Notes

Tests use mock providers (`tests/mocks/`) — `MockLLMProvider` and `MockRBClient` — so no external API calls are needed. Test data fixtures live in `tests/data/`. CLI tests use Click's `CliRunner` with isolated filesystems.
