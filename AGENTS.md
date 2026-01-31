# BB Review - AI Code Review for Review Board

## Commit Convention

Use semantic commit messages with the following prefixes:

- `feat:` - New features or capabilities
- `fix:` - Bug fixes
- `refactor:` - Code restructuring without changing behavior
- `docs:` - Documentation changes
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks, dependency updates

Examples:
```
feat: add body_top editing in interactive TUI
fix: skip general issues without file/line during import
refactor: extract comment formatting into separate function
```

Keep the first line under 72 characters. Add a blank line and detailed description if needed.

## Project Overview

BB Review is a CLI tool that provides AI-powered code reviews for Review Board (RB). It fetches diffs from Review Board, analyzes them using an LLM, and posts review comments back.

## Architecture

```text
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Review Board   │────▶│  bb_review   │────▶│  LLM API    │
│  (RB Server)    │◀────│  CLI Tool    │◀────│ (OpenRouter)│
└─────────────────┘     └──────────────┘     └─────────────┘
                              │
                              ├─────────────────┐
                              ▼                 ▼
                        ┌──────────────┐  ┌──────────────┐
                        │  Local Git   │  │  OpenCode    │
                        │  Repos Cache │  │  (optional)  │
                        └──────────────┘  └──────────────┘
                                                │
                                                ▼
                                          ┌──────────────┐
                                          │  CocoIndex   │
                                          │  MCP Server  │
                                          └──────────────┘
```

## Key Files

### Core Modules (`bb_review/`)

| File | Purpose |
|------|---------|
| `cli.py` | Click-based CLI commands (`analyze`, `opencode`, `poll`, `repos`, `cocoindex`, `submit`, `db`) |
| `rb_client.py` | Review Board API client using `curl` for Kerberos auth |
| `analyzer.py` | LLM integration (OpenRouter/Anthropic/OpenAI), prompt building, response parsing |
| `commenter.py` | Formats and posts review comments to RB |
| `repo_manager.py` | Git repository management (clone, fetch, checkout with patch application) |
| `guidelines.py` | Loads `.ai-review.yaml` from repos for per-repo customization |
| `config.py` | Pydantic config models, YAML loading, environment variable resolution |
| `models.py` | Data models (ReviewResult, ReviewComment, RepoConfig, etc.) |
| `crypto.py` | Password encryption/decryption using Fernet |
| `poller.py` | Polling daemon, state database (SQLite), review tracking |
| `db/` | Reviews database module (analysis history, export, tracking) |
| `opencode_runner.py` | OpenCode agent integration, prompt building, output parsing |
| `mcp_server.py` | FastMCP server for semantic code search via CocoIndex |
| `cocoindex_indexer.py` | Repository indexing with local sentence-transformers embeddings |

### Configuration Files

| File | Purpose |
|------|---------|
| `config.yaml` | Main config (RB URL, credentials, LLM settings, repos, CocoIndex) |
| `config.example.yaml` | Template for new installations |
| `guides/{repo}.ai-review.yaml` | Per-repo review guidelines (copied to repo cache) |

### OpenCode Integration (`opencode/`)

| File | Purpose |
|------|---------|
| `te-ts-reviewer` | Custom agent for TE test suite API review |
| `te-review-command` | Custom command for API review |
| `ts-te-mcp` | MCP config for te-test-suite repos |
| `mcp-*.json` | Various MCP server configurations |

### Scripts (`scripts/`)

| File | Purpose |
|------|---------|
| `setup-cocoindex-db.sh` | Start/stop PostgreSQL+pgvector container |
| `cocoindex-server.sh` | Legacy CocoIndex server management |

## Authentication

**Important**: Review Board may sit behind Apache with Kerberos auth. The authentication flow is:

1. Apache handles Kerberos (`--negotiate -u :` in curl)
2. RB requires separate form-based login (CSRF token + POST to `/account/login/`)
3. Session cookies are stored in a temp file and reused

The `rb_client.py` uses `subprocess.run` with `curl` because:
- Python `requests` with `requests-kerberos` didn't work reliably
- Apache strips `Authorization` headers before they reach RB
- Form-based login mimics browser behavior

Password can be encrypted:

```bash
bb-review encrypt-password  # Uses api_token as encryption key
```

## LLM Integration

### Analysis Modes

1. **Direct Analysis** (`analyze` command):
   - Uses `analyzer.py` to build prompts and call LLM
   - Returns structured JSON with inline comments
   - Good for quick reviews

2. **OpenCode Analysis** (`opencode` command):
   - Runs OpenCode agent in the repository
   - Full codebase context via MCP servers
   - Better for complex reviews
   - Supports custom agents (e.g., `api-reviewer`)

### Prompt Structure

1. **System Prompt** (`SYSTEM_PROMPT` in `analyzer.py`):
   - Generic code reviewer instructions
   - JSON output schema

2. **User Prompt** (built by `_build_prompt`):
   - Review focus areas (bugs, security, etc.)
   - Severity threshold
   - Repository context (from `.ai-review.yaml`)
   - Custom rules
   - File context (surrounding code)
   - The diff itself

### Model Selection

Models are configured in `config.yaml`. Known working models:
- `x-ai/grok-code-fast-1` - Fast, follows JSON format
- `google/gemini-2.0-flash-001` - Good balance
- `anthropic/claude-sonnet-4-20250514` - High quality

**Avoid**: Models that output "thinking" in `reasoning_content` instead of structured JSON (e.g., `deepseek-r1-*`).

## Semantic Code Search (CocoIndex)

### Overview

CocoIndex provides semantic code search for OpenCode agents:
- Uses local sentence-transformers embeddings (no API calls, no rate limits)
- Stores vectors in PostgreSQL with pgvector extension
- Exposes search via MCP server

### Components

1. **Indexer** (`cocoindex_indexer.py`):
   - Chunks code files
   - Generates embeddings locally
   - Stores in PostgreSQL

2. **MCP Server** (`mcp_server.py`):
   - FastMCP-based server
   - Provides `codebase_search` and `codebase_status` tools
   - Runs as stdio MCP server for OpenCode

### Commands

```bash
# Database management
bb-review cocoindex db start/stop/status

# Indexing
bb-review cocoindex index <repo>
bb-review cocoindex index <repo> --clear  # Re-index

# MCP server
bb-review cocoindex serve <repo>

# Status
bb-review cocoindex status-db
```

## Reviews Database

### Overview

The reviews database stores complete analysis history:
- All review analyses with comments, metadata, and chain info
- Track which exact diff revision/base commit was reviewed
- Export to JSON (for submission) or Markdown (for reports)
- Query past analyses by RR ID, repository, status

### Components

1. **ReviewDatabase** (`db/review_db.py`):
   - SQLite backend at `~/.bb_review/reviews.db`
   - Tables: `analyses`, `comments`, `chains`
   - Auto-saves analyses from `analyze` and `opencode` commands

2. **Models** (`db/models.py`):
   - `StoredAnalysis` - Complete analysis with metadata
   - `StoredComment` - Individual review comment
   - `StoredChain` - Chain of dependent reviews

3. **Export** (`db/export.py`):
   - `export_to_json()` - Submission-ready JSON format
   - `export_to_markdown()` - Human-readable report

### Commands

```bash
# List and search
bb-review db list                    # Recent analyses
bb-review db list --rr 42738         # By review request
bb-review db search "memory leak"    # Search summaries

# View and export
bb-review db show 1                  # Full analysis details
bb-review db export 1 -o review.json # Export to JSON
bb-review db export 1 --format markdown

# Manage
bb-review db stats                   # Database statistics
bb-review db mark 1 --status submitted
bb-review db cleanup --older-than 90
```

### Configuration

```yaml
review_db:
  enabled: true
  path: "~/.bb_review/reviews.db"
```

## Common Tasks

### Adding a New Repository

1. Add to `config.yaml` under `repositories:`

   ```yaml
   - name: "myrepo"
     rb_repo_name: "My Repository"
     local_path: "~/repos/myrepo"
     remote_url: "git@server:org/myrepo.git"
     default_branch: "main"
   ```

2. Sync: `bb-review repos sync myrepo`
3. (Optional) Create guide: `guides/myrepo.ai-review.yaml`
4. (Optional) Init guidelines: `bb-review repos init-guidelines myrepo`

### Testing Changes

```bash
# Dry run with direct LLM
bb-review analyze {review_id} --dry-run

# Dry run with OpenCode (auto-named output: review_{id}.json)
bb-review opencode {review_id} --dry-run -O

# Dry run with custom output filename
bb-review opencode {review_id} --dry-run -o review.json

# Edit review.json, then submit
bb-review submit review.json

# Verbose logging
bb-review -v analyze {review_id} --dry-run

# Dump raw LLM response for debugging
bb-review analyze {review_id} --dry-run --dump-response /tmp/llm.txt
```

### Debugging Issues

1. **Auth failures**:
   - Check Kerberos ticket: `klist`
   - Re-encrypt password: `bb-review encrypt-password`
   - Verify cookies are being created

2. **500 errors when posting**:
   - Usually encoding issues - avoid emojis/special chars
   - Check comment text for invalid characters

3. **LLM not returning JSON**:
   - Try different model
   - Check `--dump-response` output
   - Verify prompt format

4. **Wrong diff content**:
   - Verify URL format in `rb_client.py:_fetch_raw_diff`
   - Check diff revision number

5. **CocoIndex issues**:
   - Verify PostgreSQL running: `bb-review cocoindex db status`
   - Check indexing: `bb-review cocoindex status-db`
   - View MCP logs: `tail -f ~/.bb_review/mcp-{repo}.log`

## Known Gotchas

1. **No emojis in review text** - RB server returns 500
2. **Diff URL is `/diffs/{rev}/`** not `/diffs/{rev}/patch/`
3. **RB API returns dicts**, not objects - use `.get()` not `.attribute`
4. **Use `--data-urlencode`** for curl POST data, not `-d`
5. **OpenCode run mode** - Don't use `--agent plan` for review, it doesn't output to stdout
6. **MCP server logging** - Must use stderr, stdout is for JSON-RPC protocol
7. **FastMCP banner** - Must use `show_banner=False` or it breaks MCP protocol

## Testing Commands

```bash
# Basic checks
bb-review --help
bb-review repos list
bb-review repos status
bb-review poll status

# Analysis
bb-review analyze 42738 --dry-run
bb-review opencode 42738 --dry-run

# Reviews database
bb-review db stats
bb-review db list
bb-review db list --rr 42738

# CocoIndex
bb-review cocoindex db status
bb-review cocoindex status-db
bb-review cocoindex index te-dev --clear

# Encrypt password
bb-review encrypt-password
```

## File Locations

- Config: `./config.yaml` or `~/.bb_review/config.yaml`
- Password file: `~/.bb_review/password.enc`
- State database: `~/.bb_review/state.db`
- Reviews database: `~/.bb_review/reviews.db`
- Logs: `~/.bb_review/bb_review.log`
- MCP logs: `~/.bb_review/mcp-{repo}.log`
- CocoIndex logs: `~/.bb_review/cocoindex/`
- Repo cache: Configured per-repo via `local_path`

## Dependencies

Managed with `uv`. Key deps:
- `click` - CLI framework
- `pydantic` - Config validation
- `GitPython` - Git operations
- `openai` - LLM API client (works with OpenRouter/Anthropic)
- `anthropic` - Anthropic API client
- `cryptography` - Password encryption
- `pyyaml` - Config parsing
- `fastmcp` - MCP server framework (for CocoIndex)
- `sentence-transformers` - Local embeddings (for CocoIndex)
- `psycopg2-binary` - PostgreSQL client (for CocoIndex)
