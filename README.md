# BB Review

AI-powered code review system for Review Board.

## Overview

BB Review integrates with Review Board to provide automated AI code reviews. When a designated bot user is added as a reviewer, the system analyzes the diff and posts inline comments.

## Installation

Requires [uv](https://docs.astral.sh/uv/) for package management.

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
cd bb_review
uv sync

# Or install in development mode
uv sync --dev
```

To run commands:

```bash
# Using uv run (recommended)
uv run bb-review --help

# Or activate the virtual environment
source .venv/bin/activate
bb-review --help
```

## Configuration

1. Copy `config.example.yaml` to `config.yaml`
2. Fill in your Review Board URL and API token
3. Configure your repositories
4. Set your Anthropic API key

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your settings
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
uv run bb-review poll --once
```

Run as a daemon:

```bash
uv run bb-review poll --daemon
```

### Repository Management

```bash
uv run bb-review repos list              # List configured repos
uv run bb-review repos sync              # Fetch all repos
uv run bb-review repos sync myrepo       # Fetch specific repo
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
```

## How It Works

1. Developer adds the bot user (e.g., `ai-reviewer`) as a reviewer
2. BB Review polls for pending reviews assigned to the bot
3. For each review:
   - Fetches the diff from Review Board
   - Checks out the base commit in the local repo
   - Analyzes the changes using an LLM
   - Posts inline comments back to Review Board

## License

MIT
