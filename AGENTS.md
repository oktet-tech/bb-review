# BB Review - AI Code Review for Review Board

## Project Overview

BB Review is a CLI tool that provides AI-powered code reviews for Review Board (RB). It fetches diffs from Review Board, analyzes them using an LLM, and posts review comments back.

## Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  Review Board   │────▶│  bb_review   │────▶│  LLM API    │
│  (RB Server)    │◀────│  CLI Tool    │◀────│  (OpenRouter)│
└─────────────────┘     └──────────────┘     └─────────────┘
                              │
                              ▼
                        ┌──────────────┐
                        │  Local Git   │
                        │  Repos Cache │
                        └──────────────┘
```

## Key Files

### Core Modules (`bb_review/`)

| File | Purpose |
|------|---------|
| `cli.py` | Click-based CLI commands (`analyze`, `poll`, `repos`, etc.) |
| `rb_client.py` | Review Board API client using `curl` for Kerberos auth |
| `analyzer.py` | LLM integration (OpenRouter/Anthropic), prompt building, response parsing |
| `commenter.py` | Formats and posts review comments to RB |
| `repo_manager.py` | Git repository management (clone, fetch, checkout) |
| `guidelines.py` | Loads `.ai-review.yaml` from repos |
| `config.py` | Pydantic config models, YAML loading |
| `models.py` | Data models (ReviewResult, ReviewComment, etc.) |
| `crypto.py` | Password encryption/decryption using Fernet |

### Configuration Files

| File | Purpose |
|------|---------|
| `config.yaml` | Main config (RB URL, credentials, LLM settings, repos) |
| `config.example.yaml` | Template for new installations |
| `guides/{repo}.ai-review.yaml` | Per-repo review guidelines |

## Authentication

**Important**: Review Board sits behind Apache with Kerberos auth. The authentication flow is:

1. Apache handles Kerberos (`--negotiate -u :` in curl)
2. RB requires separate form-based login (CSRF token + POST to `/account/login/`)
3. Session cookies are stored in a temp file and reused

The `rb_client.py` uses `subprocess.run` with `curl` because:
- Python `requests` with `requests-kerberos` didn't work reliably
- Apache strips `Authorization` headers before they reach RB
- Form-based login mimics browser behavior

## LLM Integration

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
- `deepseek/deepseek-r1-*` - Great analysis but outputs reasoning, not JSON

**Avoid**: Models that output "thinking" in `reasoning_content` instead of structured JSON.

## Common Tasks

### Adding a New Repository

1. Add to `config.yaml` under `repositories:`
2. Create guide file: `guides/{repo-name}.ai-review.yaml`
3. Run: `uv run bb-review repos init-guidelines {repo-name}`

### Testing Changes

```bash
# Dry run (don't post to RB)
uv run bb-review analyze {review_id} --dry-run

# With verbose logging
uv run bb-review -v analyze {review_id} --dry-run

# Dump raw LLM response for debugging
uv run bb-review analyze {review_id} --dry-run --dump-response /tmp/llm.txt
```

### Debugging Issues

1. **Auth failures**: Check Kerberos ticket (`klist`), verify cookies
2. **500 errors when posting**: Usually encoding issues - avoid emojis/special chars
3. **LLM not returning JSON**: Try different model, check `--dump-response`
4. **Wrong diff content**: Verify URL format in `_fetch_raw_diff`

## Known Gotchas

1. **No emojis in review text** - RB server returns 500
2. **Diff URL is `/diffs/{rev}/`** not `/diffs/{rev}/patch/`
3. **RB API returns dicts**, not objects - use `.get()` not `.attribute`
4. **Use `--data-urlencode`** for curl POST data, not `-d`
5. **deepseek-r1 models** put analysis in `reasoning_content`, not `content`

## Testing Commands

```bash
# Run all commands
uv run bb-review --help
uv run bb-review repos list
uv run bb-review repos status
uv run bb-review analyze 42738 --dry-run

# Encrypt password
uv run bb-review encrypt-password
```

## File Locations

- Config: `./config.yaml` or `~/.bb_review/config.yaml`
- Password file: `~/.bb_review/password.enc`
- Cookies: Temp file (auto-managed)
- Repo cache: Configured in `config.yaml` under `repositories.{name}.local_path`

## Dependencies

Managed with `uv`. Key deps:
- `click` - CLI framework
- `pydantic` - Config validation
- `GitPython` - Git operations
- `openai` - LLM API client (works with OpenRouter)
- `cryptography` - Password encryption
- `pyyaml` - Config parsing
