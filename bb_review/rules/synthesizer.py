"""Synthesize cached reviewer comments into a draft rules document."""

from collections.abc import Callable
from pathlib import Path

from ..db.mining_db import MinedComment, MiningDatabase
from .agent_runner import run_agent


class RulesDraftError(Exception):
    """Error drafting a rules document."""


def format_comments_artifact(comments: list[MinedComment]) -> str:
    """Render cached comments as a markdown artifact for the agent.

    Comments are grouped by file so recurring per-file themes are visible.
    Each entry is tagged with its RR, RR status, reviewer, and issue
    status, which the synthesis prompt uses for weighting.
    """
    by_file: dict[str, list[MinedComment]] = {}
    for c in comments:
        key = c.file_path or "(general / body comments)"
        by_file.setdefault(key, []).append(c)

    lines: list[str] = ["# Mined Reviewer Comments", ""]
    lines.append(f"Total comments: {len(comments)}")
    lines.append("")

    for file_path in sorted(by_file):
        lines.append(f"## {file_path}")
        lines.append("")
        for c in by_file[file_path]:
            loc = f":{c.line_number}" if c.line_number else ""
            status = c.issue_status or ("issue" if c.issue_opened else "comment")
            lines.append(
                f"- [RR #{c.rr_id} | {c.rr_status} | reviewer: {c.reviewer} | {status}] {file_path}{loc}"
            )
            body = c.text.strip().replace("\n", "\n  ")
            lines.append(f"  {body}")
            if c.diff_hunk:
                lines.append("  ```diff")
                for hunk_line in c.diff_hunk.splitlines():
                    lines.append(f"  {hunk_line}")
                lines.append("  ```")
        lines.append("")

    return "\n".join(lines)


def build_rules_prompt(
    repo_name: str,
    comments_artifact: str,
    existing_patterns: str | None,
) -> str:
    """Build the agent prompt for drafting repo review rules.

    Note: `comments_artifact` is included for callers that want it; the
    agent reads the same content from `.bb_review_mined_comments.md` in
    its working directory, which `draft_rules` writes before launch.
    """
    prompt = f"""You are drafting a code-review rules document for the \
repository `{repo_name}`.

The audience for this document is an AI code-review agent that will read \
it before reviewing patches against this repo. Write every rule as a \
direct instruction TO that agent -- in imperative voice, telling it what \
to flag, what NOT to flag, and what to verify. Do not describe what human \
reviewers do; tell the reviewer agent what to do.

You are given a collection of real comments that human reviewers left on \
past review requests for this repository. They are written to the file \
`.bb_review_mined_comments.md` in your current working directory -- read \
it first. The repository source code is checked out in the same directory, \
so you may (and should) open and read files to ground and verify the rules \
you write, and to extract realistic code samples for each rule.

How to interpret the comments:
- Each comment is tagged with its review request, the RR status, the \
reviewer, and an issue status.
- `issue status = resolved` -> the author agreed and fixed it. These are \
confirmed mistakes and are strong rule candidates.
- `issue status = dropped` -> the author pushed back or disagreed. Treat \
these as weak signals and as false-positive candidates.
- When a comment is followed by a fenced diff hunk, that hunk is the \
ground-truth code the reviewer was looking at -- use it to verify the rule \
and to lift a concrete code sample for the rule.
- A pattern that recurs across multiple distinct RRs matters more than a \
one-off remark.

Required style and shape for each rule:
- Imperative voice aimed at the reviewer agent. Use phrases like \
"Do NOT flag ...", "Always verify ...", "Treat X as acceptable -- it is \
intentional", "Flag any call to Y that does not ...".
- Every rule MUST include a short fenced code block (in the repo's primary \
language) that demonstrates the pattern. When useful, show both a correct \
example and a buggy example as separate fenced blocks under "Correct \
Pattern" / "Bug" sub-headings.
- Keep prose to one or two short sentences per rule. The code sample \
carries the meaning; the prose explains the verdict ("acceptable", "bug", \
"requires checking ...").
- Lift code from the actual checkout when possible. If you cannot find a \
real example, derive one from the diff hunk attached to the comment and \
say so.

Structure: produce a single Markdown document titled \
`# Draft Review Rules: {repo_name}`, followed by topic-named `##` sections \
(one per rule or tightly-related rule cluster). Do not use generic bucket \
sections like "Recurring Mistakes" or "False-Positive Candidates" -- give \
each rule a descriptive heading naming the pattern (e.g. \
`## Indirect Includes (Acceptable)`, `## Resource Cleanup`, \
`## eftest_verdict_fail (Exit Function)`).

Worked example of the required shape (style reference only, do not copy \
content):

```
## eftest_verdict_fail (Exit Function)

```c
// These functions EXIT the process -- code after them is unreachable.
// Do NOT flag "missing error handling" or "dead code" after these.
if (result != expected)
    eftest_verdict_fail("Mismatch: got %d, expected %d", result, expected);
```
```

```
## Resource Cleanup

### Correct Pattern
```c
rc = allocate_resource(&res);
if (rc != 0)
    return rc;
// ... use resource ...
cleanup:
    free_resource(res);
```

### Bug: Missing Cleanup on Error
```c
rc = allocate_resource(&res);
if (rc != 0)
    return rc;  // OK -- nothing to clean up yet
rc = do_work(res);
if (rc != 0)
    return rc;  // BUG: res is leaked
```
```
"""

    if existing_patterns:
        prompt += f"""
An existing `technical-patterns.md` already documents rules for this repo. \
Match its heading style, code-sample format, and tone. Do NOT repeat \
anything already covered there -- only output rules that are NEW relative \
to it:

<existing-technical-patterns>
{existing_patterns}
</existing-technical-patterns>
"""

    prompt += """
Output ONLY the Markdown document. Do not include narration, thinking, or \
commentary about your process.
"""
    return prompt


ARTIFACT_FILENAME = ".bb_review_mined_comments.md"


def draft_rules(
    repo_name: str,
    mining_db: MiningDatabase,
    repo_manager,
    guides_dir: Path,
    method: str = "claude",
    model: str | None = None,
    timeout: int = 600,
    binary_path: str | None = None,
    transcript_path: Path | None = None,
    max_turns: int = 40,
    run_agent_fn: Callable[..., str] = run_agent,
) -> Path:
    """Draft a rules file for a repository from its cached reviewer comments.

    Loads cached comments, checks out the repo, writes a comments artifact
    into the checkout, runs an agent, and writes the result to
    `guides/{repo_name}/draft-rules.md`.

    Args:
        repo_name: Config repository name (also the cache `repository` key).
        mining_db: Cache database holding the fetched comments.
        repo_manager: RepoManager used to clone/checkout the repo.
        guides_dir: Path to the `guides/` directory.
        method: Agent backend, 'claude' or 'codex'.
        model: Optional model override for the agent.
        timeout: Agent timeout in seconds.
        binary_path: Optional explicit agent binary path.
        transcript_path: If set, the agent transcript is saved here.
        max_turns: Max agentic turns (Claude only; Codex ignores).
        run_agent_fn: Agent runner callable (overridable for tests).

    Returns:
        Path to the written draft-rules.md file.

    Raises:
        RulesDraftError: If no comments are cached or the agent yields nothing.
    """
    comments = mining_db.get_comments_for_repo(repo_name)
    if not comments:
        raise RulesDraftError(
            f"No cached comments for '{repo_name}'. Run 'bb-review rules fetch {repo_name}' first."
        )

    repo = repo_manager.ensure_clone(repo_name)
    repo_config = repo_manager.get_repo(repo_name)
    # A previous crashed agent run can leave the checkout dirty (e.g. modified
    # pyproject.toml); without resetting, the branch switch below fails.
    repo_manager._reset_working_tree(repo, repo_name)
    repo_manager.checkout(repo_name, repo_config.default_branch)
    repo_path = repo_manager.get_local_path(repo_name)

    existing_path = guides_dir / repo_name / "technical-patterns.md"
    existing_patterns = existing_path.read_text() if existing_path.exists() else None

    artifact = format_comments_artifact(comments)
    artifact_path = repo_path / ARTIFACT_FILENAME
    artifact_path.write_text(artifact)

    prompt = build_rules_prompt(repo_name, artifact, existing_patterns)

    try:
        output = run_agent_fn(
            method=method,
            repo_path=repo_path,
            prompt=prompt,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            transcript_path=transcript_path,
            max_turns=max_turns,
        )
    finally:
        artifact_path.unlink(missing_ok=True)
        # The agent may have edited tracked files in the checkout; reset so the
        # next run can switch branches without a "would be overwritten" error.
        repo_manager._reset_working_tree(repo, repo_name)

    if not output.strip():
        raise RulesDraftError("Agent produced empty output")

    out_dir = guides_dir / repo_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "draft-rules.md"
    out_path.write_text(output)
    return out_path
