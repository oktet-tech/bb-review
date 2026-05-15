# BB Review

Big Brother AI-powered code review system for Review Board.

## Overview

BB Review integrates with Review Board to provide automated AI code reviews. When a designated bot user is added as a reviewer, the system analyzes the diff and posts inline comments.

**Key Features:**
- Multiple analysis modes: direct LLM, OpenCode agent, Claude Code CLI, or Codex CLI with full codebase context
- Semantic code search via CocoIndex (optional, uses local embeddings - no API needed)
- Reviews database for analysis history, tracking, and export
- Review/edit/submit workflow for human oversight
- Encrypted password storage for secure authentication
- Daemon mode for continuous polling

## First-Time Deployment

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- Git
- Access to a Review Board server
- An LLM API key (Anthropic, OpenRouter, or OpenAI)
- (Optional) Docker for PostgreSQL+pgvector (CocoIndex semantic search)
- (Optional) [OpenCode](https://opencode.ai) for enhanced analysis mode
- (Optional) [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI for agentic analysis mode
- (Optional) [Codex](https://github.com/openai/codex) CLI for OpenAI agentic analysis mode

### Step 1: Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2: Clone and Install

```bash
git clone <repository-url> bb_review
cd bb_review
uv sync
```

### Step 3: Create Configuration

```bash
# Generate config template
uv run bb-review init

# Edit with your settings
vi config.yaml
```

Required settings in `config.yaml`:
- `reviewboard.url` - Your Review Board server URL
- `reviewboard.api_token` - API token from Review Board (My Account > API Tokens)
- `reviewboard.bot_username` - Username that triggers reviews
- `llm.api_key` - Your LLM provider API key
- `repositories` - List of repositories to review

### Step 4: Setup Authentication

**Option A: API Token Only** (simplest)

```yaml
reviewboard:
  url: "https://your-rb-server.com"
  api_token: "your-api-token"
  bot_username: "ai-reviewer"
```

**Option B: Username/Password with Encryption** (for Kerberos environments)

```bash
# Encrypt your password
uv run bb-review encrypt-password

# Config uses encrypted file
reviewboard:
  url: "https://your-rb-server.com"
  api_token: "your-token"  # Also used as encryption key
  use_kerberos: true
  username: "your-username"
  password_file: "~/.bb_review/password.enc"
  bot_username: "ai-reviewer"
```

### Step 5: Setup Repositories

Add repositories to `config.yaml`:

```yaml
repositories:
  - name: "myproject"
    rb_repo_name: "My Project"  # As shown in Review Board
    local_path: "~/repos/myproject"
    remote_url: "git@github.com:org/myproject.git"
    default_branch: "main"
```

Clone and sync:

```bash
uv run bb-review repos sync
```

### Step 6: Test the Setup

```bash
# List configured repos
uv run bb-review repos list

# Test analysis (saves to file, doesn't post)
uv run bb-review analyze <review-id> -O

# Or just see what would be analyzed (no LLM call)
uv run bb-review analyze <review-id> --dry-run
```

### Step 7: (Optional) Setup Semantic Search

For enhanced code understanding with CocoIndex:

```bash
# Start PostgreSQL+pgvector container
./scripts/setup-cocoindex-db.sh start

# Index a repository (uses local embeddings, no API needed)
uv run bb-review cocoindex index myproject

# Verify indexing
uv run bb-review cocoindex status-db
```

### Step 8: Run the Service

**Manual review workflow:**

```bash
# Analyze and save to file
uv run bb-review analyze <review-id> -O

# Review the JSON file, then submit as draft
uv run bb-review submit _files/review_<review-id>.json

# After verifying, publish
uv run bb-review submit _files/review_<review-id>.json --publish
```

**Polling daemon:**

```bash
uv run bb-review poll daemon
```

## Usage

### Quick Start: Analyze and Submit

```bash
# 1. Analyze a review (saves to _files/review_42738.json by default)
uv run bb-review analyze 42738 -O

# 2. Review the generated JSON file, edit if needed

# 3. Submit as draft (only you can see it)
uv run bb-review submit _files/review_42738.json

# 4. After verifying in RB UI, publish to everyone
uv run bb-review submit _files/review_42738.json --publish
```

### Analysis Commands

All four analysis modes (`analyze`, `opencode`, `claude`, `codex`) work similarly:

```bash
# Run analysis, save to auto-generated file (_files/review_{id}.json)
uv run bb-review analyze 42738 -O
uv run bb-review opencode 42738 -O
uv run bb-review claude 42738 -O
uv run bb-review codex 42738 -O

# Save to custom file
uv run bb-review analyze 42738 -o my_review.json

# Dry run - show what would be analyzed without calling LLM
uv run bb-review analyze 42738 --dry-run

# Fake review - run everything but use mock LLM responses (for testing)
uv run bb-review analyze 42738 --fake-review -O
```

### Claude Code CLI

The `claude` command uses Claude Code in non-interactive (`-p`) mode with agentic
tool use. Claude can read files, run grep/glob, and execute commands in the repo
to understand context before reporting issues.

```bash
# Basic review (resolves dependency chain automatically)
uv run bb-review claude 42738 -O

# Override model (sonnet, opus, haiku)
uv run bb-review claude 42738 -m opus -O

# Increase timeout and agentic turns for complex reviews
uv run bb-review claude 42738 --timeout 900 --max-turns 25 -O

# Fallback mode if patch doesn't apply cleanly
uv run bb-review claude 42738 --fallback -O

# Accept Review Board URL instead of numeric ID
uv run bb-review claude https://rb.example.com/r/42738/ -O

# Dry run / fake review for testing
uv run bb-review claude 42738 --dry-run
uv run bb-review claude 42738 --fake-review -O
```

Configure defaults in `config.yaml`:

```yaml
claude_code:
  enabled: true
  # model: "sonnet"       # Default model override
  timeout: 600            # Seconds
  max_turns: 15           # Max agentic tool-use rounds
  binary_path: "claude"   # Path to claude binary
  allowed_tools:          # Tools Claude Code may use during review
    - Read
    - Grep
    - Glob
    - Bash
  # mcp_config: .mcp.json  # MCP servers config for semantic search
```

#### MCP Integration (Semantic Code Search)

Claude Code can use MCP servers for semantic code search via CocoIndex.
Generate a `.mcp.json` config and pass it with `--mcp-config`:

```bash
# Setup: index the repo and generate .mcp.json
uv run bb-review cocoindex index myrepo
uv run bb-review cocoindex setup myrepo --tool claude

# Review with MCP-powered code search
uv run bb-review claude 42738 --mcp-config /path/to/repo/.mcp.json -O
```

Set `mcp_config` in `config.yaml` to avoid passing `--mcp-config` every time.

### Codex CLI

The `codex` command uses OpenAI's Codex CLI in non-interactive (`exec`) mode
with sandbox isolation. Codex can read files and run commands in the repo
to understand context before reporting issues.

```bash
# Basic review (resolves dependency chain automatically)
uv run bb-review codex 42738 -O

# Override model (e.g. o3, gpt-4.1)
uv run bb-review codex 42738 -m o3 -O

# Allow write access to workspace (default: read-only sandbox)
uv run bb-review codex 42738 --sandbox workspace-write -O

# Dry run / fake review for testing
uv run bb-review codex 42738 --dry-run
uv run bb-review codex 42738 --fake-review -O
```

Configure defaults in `config.yaml`:

```yaml
codex:
  enabled: true
  # model: "o3"             # Default model override
  timeout: 300              # Seconds
  binary_path: "codex"      # Path to codex binary
  sandbox: "read-only"      # read-only or workspace-write
```

### Patch Series (Chain Review)

Review chains of dependent patches:

```bash
# Review entire chain (auto-detects dependencies via "depends on" field)
uv run bb-review analyze 42763 -O
# Output: Chain: r/42761 -> r/42762 -> r/42763

# Review only the last patch (apply earlier patches as context)
uv run bb-review analyze 42763 --review-from 42763 -O

# Review from a specific patch onwards
uv run bb-review analyze 42763 --review-from 42762 -O

# Skip chain detection, review single patch only
uv run bb-review analyze 42763 --no-chain -O

# Manual chain order (for complex dependencies)
uv run bb-review analyze 42763 --chain-file chain.txt -O

# Keep the temporary branch for debugging
uv run bb-review analyze 42763 --keep-branch -O
```

Chain file format (one RR per line):
```
42761
42762
42763
```

### Submit Command

Submit reviews to Review Board:

```bash
# Submit as draft (default) - only you can see it
uv run bb-review submit _files/review_42738.json

# Submit and publish - visible to everyone
uv run bb-review submit _files/review_42738.json --publish

# Preview what would be submitted
uv run bb-review submit _files/review_42738.json --dry-run
```

### Review JSON File Structure

The generated JSON file can be edited before submission:

```json
{
  "review_request_id": 42738,
  "body_top": "AI Review summary...",
  "comments": [
    {"file_path": "src/foo.c", "line_number": 42, "text": "Issue description..."}
  ],
  "ship_it": false,
  "unparsed_text": "Any text that couldn't be parsed into structured issues",
  "parsed_issues": [...],
  "metadata": {"created_at": "...", "model": "...", "opencode": true}
}
```

### Automated Polling

Run a single poll cycle:

```bash
uv run bb-review poll once
```

Run as a daemon:

```bash
uv run bb-review poll daemon
```

Check polling status:

```bash
uv run bb-review poll status
```

### Repository Management

```bash
uv run bb-review repos list              # List configured repos
uv run bb-review repos sync              # Fetch all repos
uv run bb-review repos sync myrepo       # Fetch specific repo
uv run bb-review repos init-guidelines myrepo  # Setup review guidelines
uv run bb-review repos clean             # Remove temp branches and reset changes
uv run bb-review repos clean --dry-run   # Show what would be cleaned
```

### Semantic Code Search (CocoIndex)

```bash
# Database management
uv run bb-review cocoindex db start      # Start PostgreSQL container
uv run bb-review cocoindex db stop       # Stop container
uv run bb-review cocoindex db status     # Check container status

# Indexing
uv run bb-review cocoindex index myrepo  # Index a repository
uv run bb-review cocoindex index myrepo --clear  # Re-index from scratch
uv run bb-review cocoindex status-db     # Show indexing status

# MCP Server
uv run bb-review cocoindex serve myrepo  # Start MCP server
uv run bb-review cocoindex setup myrepo  # Generate opencode.json for OpenCode
uv run bb-review cocoindex setup myrepo --tool claude  # Generate .mcp.json for Claude Code
```

### Reviews Database

Track analysis history, export reviews, and query past analyses:

```bash
# List analyses with filters
uv run bb-review db list                       # Recent analyses
uv run bb-review db list --rr 42738            # By review request
uv run bb-review db list --repo te-dev         # By repository
uv run bb-review db list --status draft        # By status

# Show analysis details
uv run bb-review db show 1                     # Full details with comments
uv run bb-review db show 1 --no-comments       # Summary only

# Export for submission or review
uv run bb-review db export 1                   # Export to stdout as JSON
uv run bb-review db export 1 -o review.json    # Export to file
uv run bb-review db export 1 --format markdown # Human-readable report

# Search and manage
uv run bb-review db search 42738               # Search by RR ID
uv run bb-review db search "memory leak"       # Search in summaries
uv run bb-review db stats                      # Database statistics
uv run bb-review db mark 1 --status submitted  # Update status
uv run bb-review db cleanup --older-than 90    # Remove old analyses

# Chain tracking
uv run bb-review db chain 42762_20260130_120000  # Show chain details
```

Enable in `config.yaml`:

```yaml
review_db:
  enabled: true
  path: "~/.bb_review/reviews.db"
```

### Review Queue (Triage Workflow)

Human-triaged review workflow: sync review requests from Review Board into a
local queue, triage them, then batch-process selected items.

```bash
# 1. Populate the queue from Review Board
uv run bb-review queue sync                        # Last 10 days, all pending RRs
uv run bb-review queue sync --days 30              # Look further back
uv run bb-review queue sync --repo te-dev          # Only one repository
uv run bb-review queue sync --submitter alice       # Only one author
uv run bb-review queue sync --bot-only             # Only RRs assigned to bot

# 2. Browse and triage
uv run bb-review queue list                        # All items
uv run bb-review queue list --status todo          # New items needing triage
uv run bb-review queue list --repo te-dev          # Filter by repo
uv run bb-review queue show 42738                  # Details for one RR
uv run bb-review queue stats                       # Counts by status

# 3. Mark items for processing (or ignore)
uv run bb-review queue set 42738 42739 --status next     # Queue for analysis
uv run bb-review queue set 42740 --status ignore         # Skip this one

# 4. Process queued items
uv run bb-review queue process                     # Analyze up to 5 'next' items
uv run bb-review queue process --count 10          # Process more at once
uv run bb-review queue process --dry-run           # Preview without running
uv run bb-review queue process --fake-review       # Mock analysis for testing
uv run bb-review queue process --method codex       # Use Codex instead of default
uv run bb-review queue process --model opus        # Override LLM model
uv run bb-review queue process --submit            # Auto-submit to RB after analysis

# 5. Re-triage after new diffs
uv run bb-review queue sync                        # New diff versions reset to 'todo'
uv run bb-review queue list --status todo          # See what needs attention
```

Queue states: `todo` -> `next` -> `in_progress` -> `done`/`failed`.
Items can also be set to `ignore`. Failed items can be retried via `set --status next`.

## Per-Repository Review Guides

BB Review supports two levels of per-repo review customization:

### Simple: `.ai-review.yaml`

Create `.ai-review.yaml` in your repository root (or `guides/{repo}.ai-review.yaml`
in bb-review's directory) for basic config:

```yaml
focus:
  - bugs
  - security
  - performance

ignore_paths:
  - vendor/
  - generated/

severity_threshold: medium
```

### Rich: Guide Directories

For deeper review guidance, create a directory structure under `guides/{repo}/`:

```
guides/{repo}/
  skills/{repo}.md                 # Project skill -- auto-loaded by agents
  slash-commands/{repo}-review.md  # Review protocol/workflow
  technical-patterns.md            # Correct patterns, anti-patterns, checklist
  false-positive-guide.md          # What NOT to report (optional)
  subsystem/
    subsystem.md                   # Trigger table: path/symbol -> guide file
    {name}.md                      # Per-subsystem rules (loaded on match)
```

See `guides/README.md` for the full template and instructions.

**How guides are consumed:**

- **Agent methods** (Claude/Codex/OpenCode): Skills and commands are deployed
  into the repo checkout's local agent config (e.g., `.claude/commands/`) before
  the agent launches. The agent loads them natively.
- **Direct LLM**: `guidelines.py` reads technical-patterns.md, matches subsystem
  triggers against the diff, and concatenates relevant content into the prompt.

Guide directories are gitignored -- they contain project-specific content that
stays local. Only `guides/README.md` is tracked as template documentation.

## Configuration Reference

### LLM Providers

```yaml
# Anthropic (direct)
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "${ANTHROPIC_API_KEY}"

# OpenRouter (access to multiple models)
llm:
  provider: "openrouter"
  model: "anthropic/claude-sonnet-4-20250514"
  api_key: "${OPENROUTER_API_KEY}"

# OpenAI
llm:
  provider: "openai"
  model: "gpt-4o"
  api_key: "${OPENAI_API_KEY}"
```

### Repository Types

For special repository types that need custom handling:

```yaml
repositories:
  - name: "net-drv-ts"
    rb_repo_name: "ol-net-drv-ts"
    local_path: "~/repos/net-drv-ts"
    remote_url: "git@server:org/net-drv-ts.git"
    type: "te-test-suite"  # Enables API reviewer agent
    cocoindex:
      enabled: true  # Enable semantic indexing
```

Setup MCP for te-test-suite repos:

```bash
uv run bb-review repos mcp-setup net-drv-ts
```

## How It Works

1. Developer adds the bot user (e.g., `ai-reviewer`) as a reviewer
2. BB Review polls for pending reviews assigned to the bot
3. For each review:
   - Fetches the diff from Review Board
   - Checks out the base/target commit in the local repo
   - Loads per-repo guidelines from `.ai-review.yaml` and `guides/{repo}/`
   - Analyzes the changes using an LLM (direct, OpenCode, Claude Code, or Codex)
   - Posts inline comments back to Review Board

## Troubleshooting

### Authentication Issues

```bash
# Check Kerberos ticket
klist

# Re-encrypt password if needed
uv run bb-review encrypt-password
```

### CocoIndex Issues

```bash
# Check database is running
uv run bb-review cocoindex db status

# Check indexing status
uv run bb-review cocoindex status-db

# View MCP server logs
tail -f ~/.bb_review/mcp-myrepo.log
```

### Debugging Analysis

```bash
# Verbose output
uv run bb-review -v analyze <review-id> --dry-run

# Dump raw LLM response
uv run bb-review analyze <review-id> --dump-response /tmp/llm.txt -O
uv run bb-review opencode <review-id> --dump-response /tmp/opencode.txt -O
uv run bb-review claude <review-id> --dump-response /tmp/claude.txt -O

# Save full agent conversation transcript (all tool calls, messages, costs)
uv run bb-review claude <review-id> --transcript _files/transcript.json -O
uv run bb-review codex <review-id> --transcript _files/transcript.jsonl -O
uv run bb-review opencode <review-id> --transcript _files/transcript.log -O

# Pretty-print a transcript (collapses noise, highlights tool calls and costs)
uv run bb-review transcript _files/transcript.json

# Full JSON pretty-print
uv run bb-review transcript --raw _files/transcript.json
```

## Claude Code Integration

A skill file at `.claude/skills/bb-review-notes.md` teaches Claude Code how to fetch and analyze Review Board comments. When working in a project that uses bb-review, Claude will automatically know how to:

```bash
# Fetch review comments as markdown (Claude runs this for you)
bb-review -p <profile> comments <RR_ID_OR_URL>
bb-review -p <profile> comments https://reviewboard.example.com/r/18128/ -o notes.md
bb-review -p <profile> comments 18128 --context-lines 30
```

After fetching, Claude reads the output, groups comments by theme, identifies action items vs. style nits, and helps draft responses or code fixes.

## License

MIT
