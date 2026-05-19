"""Generic agent CLI runner that returns plain text output.

Reuses the binary-discovery helpers from the review reviewers but runs the
agent without the review-specific patch-file lifecycle or output parsing.
"""

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from ..reviewers.claude_code import find_claude_binary
from ..reviewers.codex import find_codex_binary


logger = logging.getLogger(__name__)


class AgentRunError(Exception):
    """Error running an agent CLI."""


def run_agent(
    method: str,
    repo_path: Path,
    prompt: str,
    model: str | None = None,
    timeout: int = 600,
    binary_path: str | None = None,
    transcript_path: Path | None = None,
) -> str:
    """Run an agent CLI in `repo_path` and return its final text output.

    Args:
        method: 'claude' or 'codex'.
        repo_path: Working directory for the agent (a repo checkout).
        prompt: Prompt text, passed on stdin.
        model: Optional model override.
        timeout: Timeout in seconds.
        binary_path: Optional explicit binary path.
        transcript_path: If set, the raw agent output is saved here.

    Returns:
        The agent's final text output.

    Raises:
        AgentRunError: For unknown method, non-zero exit, timeout, or empty
            output.
    """
    if method == "claude":
        return _run_claude(repo_path, prompt, model, timeout, binary_path or "claude", transcript_path)
    if method == "codex":
        return _run_codex(repo_path, prompt, model, timeout, binary_path or "codex", transcript_path)
    raise AgentRunError(f"Unknown agent method: {method!r} (expected 'claude' or 'codex')")


def _run_claude(
    repo_path: Path,
    prompt: str,
    model: str | None,
    timeout: int,
    binary_path: str,
    transcript_path: Path | None,
) -> str:
    """Run Claude Code in headless mode and return its result text."""
    claude_bin = find_claude_binary(binary_path)
    cmd = [
        claude_bin,
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        "40",
        "--allowedTools",
        "Read,Grep,Glob,Bash",
    ]
    if model:
        cmd.extend(["--model", model])
    if transcript_path:
        cmd.append("--verbose")

    logger.info(f"Running Claude Code in {repo_path}")
    print(f"  Command: {' '.join(cmd)}", file=sys.stderr)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise AgentRunError(f"Claude Code timed out after {timeout}s") from e

    if result.returncode != 0:
        raise AgentRunError(
            f"Claude Code exited with code {result.returncode}: "
            f"{result.stderr or result.stdout or 'unknown error'}"
        )

    output = result.stdout.strip()
    if not output:
        raise AgentRunError("Claude Code returned empty output")
    if transcript_path:
        transcript_path.write_text(output)

    try:
        envelope = json.loads(output)
        if isinstance(envelope, list):
            envelope = envelope[-1] if envelope else {}
        text = envelope.get("result", "")
    except json.JSONDecodeError as e:
        raise AgentRunError(f"Failed to parse Claude Code JSON output: {e}") from e

    if not text:
        raise AgentRunError("Claude Code produced no result text")
    return text


def _run_codex(
    repo_path: Path,
    prompt: str,
    model: str | None,
    timeout: int,
    binary_path: str,
    transcript_path: Path | None,
) -> str:
    """Run Codex in read-only sandbox and return its last message."""
    codex_bin = find_codex_binary(binary_path)
    fd, output_path = tempfile.mkstemp(suffix=".txt", prefix="bb_review_rules_codex_")
    os.close(fd)

    try:
        cmd = [codex_bin, "exec", "-s", "read-only", "-o", output_path]
        if model:
            cmd.extend(["-m", model])
        if transcript_path:
            cmd.append("--json")
        cmd.append("-")

        logger.info(f"Running Codex in {repo_path}")
        print(f"  Command: {' '.join(cmd)}", file=sys.stderr)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(repo_path),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise AgentRunError(f"Codex timed out after {timeout}s") from e

        if result.returncode != 0:
            raise AgentRunError(
                f"Codex exited with code {result.returncode}: "
                f"{result.stderr or result.stdout or 'unknown error'}"
            )

        if transcript_path and result.stdout:
            transcript_path.write_text(result.stdout)

        out_file = Path(output_path)
        output = out_file.read_text().strip() if out_file.exists() else ""
        if not output:
            output = result.stdout.strip()
        if not output:
            raise AgentRunError("Codex returned empty output")
        return output
    finally:
        Path(output_path).unlink(missing_ok=True)
