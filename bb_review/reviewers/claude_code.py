"""Claude Code CLI runner for code review."""

import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys


logger = logging.getLogger(__name__)


class ClaudeCodeError(Exception):
    """Error running Claude Code CLI."""

    pass


class ClaudeCodeNotFoundError(ClaudeCodeError):
    """Claude Code binary not found."""

    pass


class ClaudeCodeTimeoutError(ClaudeCodeError):
    """Claude Code execution timed out."""

    pass


def find_claude_binary(binary_path: str = "claude") -> str:
    """Find the claude binary, raising if not found.

    Returns:
        Resolved path to the binary.

    Raises:
        ClaudeCodeNotFoundError: If binary cannot be found.
    """
    if Path(binary_path).is_absolute():
        if Path(binary_path).exists():
            return binary_path
        raise ClaudeCodeNotFoundError(f"Claude Code binary not found at: {binary_path}")

    resolved = shutil.which(binary_path)
    if resolved:
        return resolved

    raise ClaudeCodeNotFoundError(
        f"Claude Code binary '{binary_path}' not found in PATH. "
        "Install it with: npm install -g @anthropic-ai/claude-code"
    )


def check_claude_available(binary_path: str = "claude") -> tuple[bool, str]:
    """Check if claude CLI is available.

    Returns:
        Tuple of (is_available, message).
    """
    try:
        claude_bin = find_claude_binary(binary_path)

        result = subprocess.run(
            [claude_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            version = result.stdout.strip() or "unknown version"
            return True, f"Claude Code available: {version}"
        else:
            return False, f"Claude Code found but --version failed: {result.stderr}"

    except ClaudeCodeNotFoundError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, "Claude Code --version timed out"
    except Exception as e:
        return False, f"Error checking Claude Code: {e}"


def build_review_prompt(
    repo_name: str,
    review_id: int,
    summary: str,
    guidelines_context: str,
    focus_areas: list[str],
    at_reviewed_state: bool = False,
    changed_files: list[str] | None = None,
    verbose: bool = False,
) -> str:
    """Build the review prompt for Claude Code.

    Args:
        repo_name: Name of the repository.
        review_id: Review Board request ID.
        summary: Review request summary/description.
        guidelines_context: Context from .ai-review.yaml.
        focus_areas: List of areas to focus on (bugs, security, etc.).
        at_reviewed_state: If True, repo is at reviewed state (files staged).
        changed_files: List of files changed in the review.
        verbose: If True, request detailed multi-paragraph explanations.

    Returns:
        Formatted prompt string.
    """
    focus_str = ", ".join(focus_areas) if focus_areas else "bugs, security, performance"

    if at_reviewed_state and changed_files:
        files_list = "\n".join(f"- `{f}`" for f in changed_files)
        prompt = f"""You are reviewing a code change. The changes are staged in this repository.

Repository: {repo_name}
Review Request: #{review_id}
Description: {summary}

Changed files:
{files_list}

To review effectively:
1. Run `git diff --cached` to see exactly what was changed (this shows the staged diff)
2. Read the changed files directly to see full context with correct line numbers
3. Line numbers in your findings must match the actual file line numbers

Do NOT use any attached patch file - use `git diff --cached` instead.
"""
    else:
        prompt = f"""You are reviewing a code change. The patch could not be applied to the \
repository, so you must analyze the patch file directly.

Repository: {repo_name}
Review Request: #{review_id}
Description: {summary}

To review effectively:
1. Read the patch file at `.bb_review_patch.diff` to see the changes
2. Read the affected files in the repository to understand context \
(note: they show the OLD version before the patch)
3. Line numbers in your findings must match the NEW line numbers shown in the patch \
(lines starting with +)
"""

    if guidelines_context:
        prompt += f"""
Guidelines:
{guidelines_context}
"""

    prompt += f"""
Focus areas: {focus_str}

Please analyze this code change for:
- Bugs and logic errors
- Security vulnerabilities
- Performance issues
- Code quality concerns

For each issue found, use this format:

### Issue: <brief title>
- **File:** `path/to/file.c`
- **Line:** <actual line number in the file>
- **Severity:** low/medium/high/critical
- **Type:** bug/security/performance/style/architecture
- **Comment:** <description of the issue>
- **Suggestion:** <optional suggested fix>

For general observations that don't apply to a specific line, omit the Line field.

After listing all issues, provide a brief summary of the overall code quality.

Do not suggest changes outside the scope of the review.
Output ONLY the structured review (### Issue blocks and summary). \
Do not include introductory text, thinking, or narration of your process."""

    if verbose:
        prompt += """

Write thorough, multi-paragraph explanations in each Comment field. \
Include step-by-step reasoning, concrete examples, memory layouts, \
and control flow analysis where relevant. \
Explain the root cause in detail, not just the symptom."""
    else:
        prompt = prompt.replace(
            "Do not suggest changes",
            "Be concise but thorough. Do not suggest changes",
        )

    return prompt


SYSTEM_PROMPT = """\
You are a senior code reviewer. Analyze code changes and report issues using the \
### Issue: format. Be precise with file paths and line numbers. Focus on real problems, \
not style nitpicks unless asked."""


def run_claude_review(
    repo_path: Path,
    patch_content: str,
    prompt: str,
    model: str | None = None,
    timeout: int = 600,
    max_turns: int = 15,
    binary_path: str = "claude",
    allowed_tools: list[str] | None = None,
    at_reviewed_state: bool = False,
    mcp_config: Path | None = None,
) -> str:
    """Run Claude Code CLI and return the analysis.

    Args:
        repo_path: Path to the repository.
        patch_content: The raw diff/patch content.
        prompt: The review prompt.
        model: Optional model override (e.g., "sonnet", "opus").
        timeout: Timeout in seconds.
        max_turns: Max agentic turns.
        binary_path: Path to the claude binary.
        allowed_tools: List of tools Claude is allowed to use.
        at_reviewed_state: If True, changes are staged - don't write patch file.
        mcp_config: Path to MCP servers config file (e.g. .mcp.json).

    Returns:
        The analysis text from Claude Code.

    Raises:
        ClaudeCodeNotFoundError: If binary is not found.
        ClaudeCodeTimeoutError: If execution times out.
        ClaudeCodeError: For other execution errors.
    """
    claude_bin = find_claude_binary(binary_path)
    logger.debug(f"Using Claude Code binary: {claude_bin}")

    # Write patch file if not at reviewed state
    patch_path = None
    if not at_reviewed_state:
        patch_path = repo_path / ".bb_review_patch.diff"
        patch_path.write_text(patch_content)

    try:
        cmd = [
            claude_bin,
            "-p",
            "--output-format",
            "json",
            "--max-turns",
            str(max_turns),
        ]

        if model:
            cmd.extend(["--model", model])

        cmd.extend(["--append-system-prompt", SYSTEM_PROMPT])

        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

        if mcp_config:
            cmd.extend(["--mcp-config", str(mcp_config)])

        logger.info(f"Running Claude Code in {repo_path}")
        print(f"  Command: {' '.join(cmd)}", file=sys.stderr)
        logger.debug(f"Full command: {cmd}")

        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.stderr:
            logger.debug(f"Claude Code stderr: {result.stderr}")
            print(f"  Claude Code stderr: {result.stderr[:500]}", file=sys.stderr)

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise ClaudeCodeError(f"Claude Code exited with code {result.returncode}: {error_msg}")

        output = result.stdout.strip()
        if not output:
            raise ClaudeCodeError("Claude Code returned empty output")

        # Unwrap JSON envelope - claude -p --output-format json returns
        # {"type": "result", "subtype": "success", "result": "...", ...}
        try:
            envelope = json.loads(output)
            subtype = envelope.get("subtype", "")
            text = envelope.get("result", "")

            if text:
                if subtype == "error_max_turns":
                    num_turns = envelope.get("num_turns", "?")
                    logger.warning(f"Claude Code hit max turns ({num_turns}) but produced output, using it")
                logger.info(f"Claude Code analysis complete ({len(text)} chars)")
                return text

            # No result text
            if subtype == "error_max_turns":
                num_turns = envelope.get("num_turns", "?")
                raise ClaudeCodeError(
                    f"Claude Code hit max turns limit ({num_turns} turns) "
                    f"without producing a review. Try increasing --max-turns."
                )
            raise ClaudeCodeError(f'Claude Code JSON response has no "result" field: {output[:200]}')
        except json.JSONDecodeError as e:
            raise ClaudeCodeError(f"Failed to parse Claude Code JSON output: {e}") from e

    except subprocess.TimeoutExpired as e:
        raise ClaudeCodeTimeoutError(f"Claude Code execution timed out after {timeout} seconds") from e

    finally:
        if patch_path and patch_path.exists():
            try:
                patch_path.unlink()
            except Exception:
                pass
