# Skill: Analyze Review Board Notes with bb-review

## When to activate

User asks to fetch, read, or analyze review comments/notes from Review Board.
Trigger phrases: "review notes", "review comments", "RB comments", "what did reviewers say", review board URLs.

## Fetching comments

```bash
# Basic (writes comments_{id}.md)
bb-review -p <profile> comments <RR_ID_OR_URL>

# Custom output
bb-review -p <profile> comments <RR_ID_OR_URL> -o notes.md

# More source context around each comment
bb-review -p <profile> comments <RR_ID_OR_URL> --context-lines 30
```

REVIEW_ID accepts a plain number (`18128`) or a full URL (`https://reviewboard.example.com/r/18128/`).

The profile flag (`-p`) selects `~/.bb_review/<name>/config.yaml`. Ask the user which profile to use if unclear.

## Output format

The command produces a markdown file with:
- Header: RR id, repository name, diff revision
- Per inline comment: file:line, reviewer name, source context block, comment text
- General (body) comments at the end

## Workflow

1. Run `bb-review comments` to fetch into a markdown file.
2. Read the output file.
3. Analyze the comments: summarize, group by theme, identify action items.
4. When the user asks, help draft responses or code fixes.

## Analyzing comments

When reading the comments file:
- Group by file or by reviewer, depending on what the user needs.
- Distinguish actionable feedback (bugs, requested changes) from style nits and questions.
- If source context is present, read the actual source files for deeper understanding.
- Flag comments that contradict each other.

## Related commands

```bash
# Triage comments via LLM (classify + plan fixes)
bb-review -p <profile> triage <RR_ID> --no-tui -O

# Interactive TUI for managing reviews
bb-review -p <profile> interactive
```
