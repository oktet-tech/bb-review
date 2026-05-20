"""Generic agent CLI runner that returns plain text output.

Reuses the binary-discovery helpers from the review reviewers but runs the
agent without the review-specific patch-file lifecycle or output parsing.
"""

from collections.abc import Callable, Iterable
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from ..reviewers.claude_code import find_claude_binary
from ..reviewers.codex import find_codex_binary


logger = logging.getLogger(__name__)


class AgentRunError(Exception):
    """Error running an agent CLI."""


DEFAULT_MAX_TURNS = 40


def run_agent(
    method: str,
    repo_path: Path,
    prompt: str,
    model: str | None = None,
    timeout: int = 600,
    binary_path: str | None = None,
    transcript_path: Path | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
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
        max_turns: Max agentic turns. Only applied for 'claude' (Codex CLI
            has no equivalent flag).

    Returns:
        The agent's final text output.

    Raises:
        AgentRunError: For unknown method, non-zero exit, timeout, or empty
            output.
    """
    if method == "claude":
        return _run_claude(
            repo_path, prompt, model, timeout, binary_path or "claude", transcript_path, max_turns
        )
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
    max_turns: int,
) -> str:
    """Run Claude Code in headless streaming mode and return its result text.

    Uses `--output-format stream-json --verbose` so we can render per-turn
    progress to stderr while the agent works, instead of buffering for the
    full duration and looking hung.
    """
    claude_bin = find_claude_binary(binary_path)
    cmd = [
        claude_bin,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(max_turns),
        "--allowedTools",
        "Read,Grep,Glob,Bash",
    ]
    if model:
        cmd.extend(["--model", model])

    logger.info(f"Running Claude Code in {repo_path}")
    print(f"  Command: {' '.join(cmd)}", file=sys.stderr)

    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    timed_out = False

    def _kill() -> None:
        nonlocal timed_out
        timed_out = True
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

    watchdog = threading.Timer(timeout, _kill)
    watchdog.start()
    start = time.monotonic()

    try:
        assert proc.stdin is not None
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        assert proc.stdout is not None

        def _on_progress(line: str) -> None:
            print(line, file=sys.stderr)

        result_text, transcript_lines = _consume_claude_stream(proc.stdout, _on_progress, start)
        proc.wait()
    finally:
        watchdog.cancel()

    if timed_out:
        raise AgentRunError(f"Claude Code timed out after {timeout}s")

    if proc.returncode != 0:
        tail = "\n".join(transcript_lines[-5:]) or "unknown error"
        raise AgentRunError(f"Claude Code exited with code {proc.returncode}: {tail}")

    if transcript_path:
        transcript_path.write_text("\n".join(transcript_lines) + "\n")

    if not result_text:
        raise AgentRunError("Claude Code produced no result text")
    return result_text


def _consume_claude_stream(
    lines: Iterable[str],
    on_progress: Callable[[str], None],
    start_monotonic: float,
) -> tuple[str, list[str]]:
    """Parse stream-json output from Claude Code.

    Drives per-turn progress callbacks and extracts the final result text.

    Args:
        lines: Iterable of raw stdout lines (newline-stripped or not).
        on_progress: Callable invoked once per assistant turn with a
            user-facing one-line summary.
        start_monotonic: `time.monotonic()` captured before launch; used to
            stamp elapsed seconds in progress lines.

    Returns:
        Tuple of (final_result_text, all_raw_lines).
    """
    raw: list[str] = []
    result_text = ""
    turn = 0
    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        raw.append(stripped)
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        if etype == "assistant":
            turn += 1
            msg = event.get("message") or {}
            content = msg.get("content") or []
            tools: list[str] = []
            texts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    tools.append(str(block.get("name", "?")))
                elif btype == "text":
                    text = block.get("text", "")
                    if text:
                        texts.append(text)
            elapsed = int(time.monotonic() - start_monotonic)
            if tools:
                detail = f"tool: {', '.join(tools)}"
            elif texts:
                first_line = texts[0].strip().splitlines()[0] if texts[0].strip() else ""
                detail = (first_line[:80] + "...") if len(first_line) > 80 else first_line or "<empty>"
            else:
                detail = "(no content)"
            on_progress(f"  [turn {turn}, {elapsed}s] {detail}")
        elif etype == "result":
            result_text = event.get("result", "") or result_text
    return result_text, raw


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
