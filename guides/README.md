# Per-Repo Review Guides

Structured review guidance for AI-powered code reviews, inspired by
[review-prompts](https://github.com/masoncl/review-prompts/).

## Directory Structure

Each repository gets a directory under `guides/`:

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

An optional `guides/{repo}.ai-review.yaml` coexists for structured config
(focus areas, severity threshold, ignore paths).

## File Descriptions

### `skills/{repo}.md`

Project description and context. Loaded automatically by agents (Claude Code,
Codex, OpenCode) when deployed into the repo checkout. Contains:

- What the project is, its domain, key technologies
- Activation criteria (directory markers, file patterns)
- Available capabilities (review, debug, etc.)
- Key conventions that differ from general expectations

### `slash-commands/{repo}-review.md`

The review protocol. Describes the step-by-step workflow the reviewer should
follow. References `technical-patterns.md` and `subsystem/subsystem.md` for
loading additional context. Contains:

- What to read first (technical patterns, subsystem guides)
- Critical checks specific to this codebase
- Output format expectations
- Review severity and focus guidance

### `technical-patterns.md`

The core review checklist. Organized by category (error handling, memory
management, API usage, etc.). Each section lists:

- Correct patterns with examples
- Anti-patterns to flag
- Common mistakes specific to this codebase

### `false-positive-guide.md`

Patterns that look like issues but are intentional or acceptable. Prevents
the reviewer from wasting time on non-issues. Format:

- Pattern description
- Why it's acceptable
- When it IS actually a problem (edge cases)

### `subsystem/subsystem.md`

A trigger table mapping file paths and symbols to subsystem guide files.
Format is a markdown table:

```markdown
| Subsystem | Triggers | File |
|-----------|----------|------|
| Sockets   | socket/, sock_, SOCK_ | sockets.md |
| Config    | cfg_, configurator, rollback | config.md |
```

**Triggers** can be directory paths, function prefixes, or symbol patterns.
When a diff touches files/symbols matching a trigger, the corresponding
subsystem guide is loaded.

### `subsystem/{name}.md`

Per-subsystem rules, invariants, API contracts, and common bug patterns.
Loaded conditionally based on trigger matches.

## How Guides Are Consumed

### Agent methods (Claude Code, Codex, OpenCode)

Skills and slash-commands are deployed into the repo checkout's local agent
config directory (e.g., `.claude/commands/`) before the agent launches. The
agent loads them natively. The agent reads `subsystem/subsystem.md` and
decides which subsystem guides to load based on the diff.

### Direct LLM method

`guidelines.py` reads all relevant `.md` files and concatenates them into
the prompt context. Subsystem triggers are pre-matched against changed files
from the diff, and only relevant subsystem guides are included.

## Creating a New Repo Guide

1. Create `guides/{repo}/` directory
2. Create `skills/{repo}.md` -- describe the project
3. Create `slash-commands/{repo}-review.md` -- define the review workflow
4. Create `technical-patterns.md` -- list correct/incorrect patterns
5. Create `subsystem/subsystem.md` -- add trigger table (can start empty)
6. Optionally create `guides/{repo}.ai-review.yaml` for structured config
7. Run `bb-review repos sync` to deploy guidelines to repo checkouts

Start with stubs and fill in as you learn more about the codebase.
