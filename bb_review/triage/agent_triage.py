"""Claude Code and OpenCode triage runners.

Runs triage analysis via external CLI tools (claude, opencode) instead of
direct LLM API calls. Parses the response using the same parse logic as
TriageAnalyzer.
"""

import json
import logging
import shutil
import subprocess
import sys

from .analyzer import TRIAGE_SYSTEM_PROMPT, parse_triage_response
from .models import RBComment, TriageResult


logger = logging.getLogger(__name__)


def build_triage_prompt(
    comments: list[RBComment],
    diff: str,
    file_contexts: dict[str, str] | None = None,
    guidelines_text: str = "",
) -> str:
    """Build the user prompt for triage (same structure as TriageAnalyzer._build_prompt)."""
    parts: list[str] = []

    if guidelines_text:
        parts.append(f"## Repository Guidelines\n{guidelines_text}")

    parts.append(f"## Diff Under Review\n```diff\n{diff}\n```")

    if file_contexts:
        parts.append("## File Context")
        for path, context in file_contexts.items():
            parts.append(f"### {path}\n```\n{context}\n```")

    parts.append("## Comments to Triage")
    for c in comments:
        location = ""
        if c.file_path:
            location = f" ({c.file_path}"
            if c.line_number:
                location += f":{c.line_number}"
            location += ")"
        kind = "body comment" if c.is_body_comment else "diff comment"
        issue = " [issue]" if c.issue_opened else ""
        parts.append(
            f'- comment_id={c.comment_id}, reviewer={c.reviewer}, type={kind}{issue}{location}\n  "{c.text}"'
        )

    parts.append(
        "\n## Instructions\n"
        "Classify each comment above and respond as JSON. "
        "Use the exact comment_id values provided."
    )

    return "\n\n".join(parts)


def run_claude_triage(
    prompt: str,
    comments: list[RBComment],
    rr_id: int,
    *,
    model: str | None = None,
    max_turns: int = 3,
) -> TriageResult:
    """Run triage via Claude Code CLI.

    Args:
        prompt: The triage prompt text.
        comments: Original RB comments for response parsing.
        rr_id: Review request ID.
        model: Model name override (e.g. 'opus').
        max_turns: Max agentic turns for claude.
    """
    binary = shutil.which("claude")
    if not binary:
        binary = str(__import__("pathlib").Path(sys.executable).parent / "claude")

    cmd = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--append-system-prompt",
        TRIAGE_SYSTEM_PROMPT,
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)

    logger.info(f"Running claude triage ({len(prompt)} chars prompt, model={model or 'default'})")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        logger.error(f"Claude triage failed (rc={result.returncode}): {result.stderr[:500]}")
        raise RuntimeError(f"Claude CLI exited with code {result.returncode}")

    # Claude --output-format json wraps response in a JSON envelope
    response_text = _extract_claude_response(result.stdout)
    logger.info(f"Claude triage response: {len(response_text)} chars")

    return parse_triage_response(response_text, comments, rr_id)


def run_opencode_triage(
    prompt: str,
    comments: list[RBComment],
    rr_id: int,
    *,
    model: str | None = None,
    max_turns: int = 3,
) -> TriageResult:
    """Run triage via OpenCode CLI.

    Same interface as run_claude_triage but uses the opencode binary.
    """
    binary = shutil.which("opencode")
    if not binary:
        binary = str(__import__("pathlib").Path(sys.executable).parent / "opencode")

    cmd = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--append-system-prompt",
        TRIAGE_SYSTEM_PROMPT,
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)

    logger.info(f"Running opencode triage ({len(prompt)} chars prompt)")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        logger.error(f"OpenCode triage failed (rc={result.returncode}): {result.stderr[:500]}")
        raise RuntimeError(f"OpenCode CLI exited with code {result.returncode}")

    response_text = _extract_claude_response(result.stdout)
    logger.info(f"OpenCode triage response: {len(response_text)} chars")

    return parse_triage_response(response_text, comments, rr_id)


def _extract_claude_response(stdout: str) -> str:
    """Extract the text content from Claude's JSON output envelope.

    Claude --output-format json returns something like:
    {"type":"result","result":"...the actual text..."}
    """
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            return data.get("result", "") or data.get("content", "") or stdout
        return stdout
    except (json.JSONDecodeError, ValueError):
        # Not JSON, return raw output
        return stdout
