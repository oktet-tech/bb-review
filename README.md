# BB Review

Big Brother AI-powered code review system for Review Board.

## Overview

BB Review integrates with Review Board to provide automated AI code reviews. When a designated bot user is added as a reviewer, the system analyzes the diff and posts inline comments.

**Key Features:**
- Multiple analysis modes: direct LLM analysis or OpenCode agent with full codebase context
- Semantic code search via CocoIndex (optional, uses local embeddings - no API needed)
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

# Test analysis (dry run)
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

**Manual review:**
```bash
uv run bb-review analyze <review-id>
```

**Polling daemon:**
```bash
uv run bb-review poll daemon
```

## Usage

### Manual Review

Analyze a specific review request:

```bash
uv run bb-review analyze 18080
uv run bb-review analyze 18080 --dry-run  # Preview without posting
```

### OpenCode Analysis

Use OpenCode agent for deeper analysis with full codebase context:

```bash
uv run bb-review opencode 42738
uv run bb-review opencode 42738 --dry-run  # Save to review_42738.json
uv run bb-review opencode 42738 --dry-run -o review.json  # Custom output file
```

### Review, Edit, and Submit Workflow

For more control, you can review and edit AI feedback before posting:

```bash
# 1. Run analysis in dry-run mode (saves JSON file)
uv run bb-review opencode 42738 --dry-run -o review.json

# 2. Review and edit the JSON file
#    - Edit comments, body_top, or remove unwanted issues
#    - The unparsed_text field contains any LLM output that couldn't be parsed

# 3. Preview what would be submitted
uv run bb-review submit review.json --dry-run

# 4. Submit to ReviewBoard
uv run bb-review submit review.json
```

The JSON file structure:

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
  "metadata": {"created_at": "...", "model": "...", "dry_run": true}
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

# MCP Server (for OpenCode integration)
uv run bb-review cocoindex serve myrepo  # Start MCP server
uv run bb-review cocoindex setup myrepo  # Generate opencode.json for repo
```

## Per-Repository Configuration

Create `.ai-review.yaml` in your repository root to customize review behavior:

```yaml
focus:
  - bugs
  - security
  - performance

context: |
  This is a C network stack. Focus on memory safety,
  buffer overflows, and proper error handling.

ignore_paths:
  - vendor/
  - generated/

severity_threshold: medium

custom_rules:
  - Always check return values of memory allocation functions
  - Ensure all network buffers are properly bounded
```

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
   - Loads per-repo guidelines from `.ai-review.yaml`
   - Analyzes the changes using an LLM (direct or via OpenCode)
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
uv run bb-review analyze <review-id> --dry-run --dump-response /tmp/llm.txt
uv run bb-review opencode <review-id> --dry-run --dump-response /tmp/opencode.txt
```

## License

MIT
