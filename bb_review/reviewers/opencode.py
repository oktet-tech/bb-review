"""OpenCode agent runner for code review."""

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys


logger = logging.getLogger(__name__)


class OpenCodeError(Exception):
    """Error running OpenCode."""

    pass


class OpenCodeNotFoundError(OpenCodeError):
    """OpenCode binary not found."""

    pass


class OpenCodeTimeoutError(OpenCodeError):
    """OpenCode execution timed out."""

    pass


def find_opencode_binary(binary_path: str = "opencode") -> str:
    """Find the opencode binary, raising if not found.

    Args:
        binary_path: Path or name of the opencode binary.

    Returns:
        Resolved path to the binary.

    Raises:
        OpenCodeNotFoundError: If binary cannot be found.
    """
    # If it's an absolute path, check it exists
    if Path(binary_path).is_absolute():
        if Path(binary_path).exists():
            return binary_path
        raise OpenCodeNotFoundError(f"OpenCode binary not found at: {binary_path}")

    # Try to find in PATH
    resolved = shutil.which(binary_path)
    if resolved:
        return resolved

    raise OpenCodeNotFoundError(
        f"OpenCode binary '{binary_path}' not found in PATH. "
        "Install it with: curl -fsSL https://opencode.ai/install | bash"
    )


def build_review_prompt(
    repo_name: str,
    review_id: int,
    summary: str,
    guidelines_context: str,
    focus_areas: list[str],
    at_reviewed_state: bool = False,
    changed_files: list[str] | None = None,
) -> str:
    """Build the review prompt for OpenCode.

    Args:
        repo_name: Name of the repository.
        review_id: Review Board request ID.
        summary: Review request summary/description.
        guidelines_context: Context from .ai-review.yaml.
        focus_areas: List of areas to focus on (bugs, security, etc.).
        at_reviewed_state: If True, repo is at reviewed state (files match new version).
        changed_files: List of files changed in the review.

    Returns:
        Formatted prompt string.
    """
    focus_str = ", ".join(focus_areas) if focus_areas else "bugs, security, performance"

    if at_reviewed_state and changed_files:
        # Repo has the reviewed code staged - tell OpenCode to use git diff --cached
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
        prompt = f"""You are reviewing a code change. The patch could not be applied to the repository,
so you must analyze the patch file directly.

Repository: {repo_name}
Review Request: #{review_id}
Description: {summary}

To review effectively:
1. Read the patch file at `.bb_review_patch.diff` to see the changes
2. Read the affected files in the repository to understand context
   (note: they show the OLD version before the patch)
3. Line numbers in your findings must match the NEW line numbers shown in the patch
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

Be concise but thorough. Do not suggest changes outside the scope of the review."""

    return prompt


def run_opencode_review(
    repo_path: Path,
    patch_content: str,
    prompt: str,
    review_id: int,
    model: str | None = None,
    timeout: int = 300,
    binary_path: str = "opencode",
    at_reviewed_state: bool = False,
) -> str:
    """Run opencode and return the analysis.

    Args:
        repo_path: Path to the repository to run opencode in.
        patch_content: The raw diff/patch content.
        prompt: The review prompt to send to opencode.
        review_id: Review Board request ID (used for session title).
        model: Optional model override (e.g., "anthropic/claude-sonnet-4-20250514").
        timeout: Timeout in seconds for the opencode process.
        binary_path: Path to the opencode binary.
        at_reviewed_state: If True, changes are staged - don't pass patch file.

    Returns:
        The analysis output from opencode.

    Raises:
        OpenCodeNotFoundError: If opencode binary is not found.
        OpenCodeTimeoutError: If execution times out.
        OpenCodeError: For other execution errors.
    """
    # Verify binary exists
    opencode_bin = find_opencode_binary(binary_path)
    logger.debug(f"Using opencode binary: {opencode_bin}")

    # Create temp files in the repo directory so @filename syntax works
    # (opencode resolves @ paths relative to cwd)
    patch_path = None
    prompt_path = repo_path / ".bb_review_prompt.md"

    # Write prompt to file in repo directory
    prompt_path.write_text(prompt)

    if not at_reviewed_state:
        # Create patch file in repo directory
        patch_path = repo_path / ".bb_review_patch.diff"
        patch_path.write_text(patch_content)

    try:
        # Build command
        # Note: Don't use --agent plan as it doesn't produce stdout output
        cmd = [
            opencode_bin,
            "run",
            "--title",
            f"Review-{review_id}",
        ]

        if model:
            cmd.extend(["--model", model])

        # Use @filename to include the prompt - file must be relative to cwd (repo_path)
        # The prompt itself references the patch file when in fallback mode
        cmd.append("@.bb_review_prompt.md")

        logger.info(f"Running opencode in {repo_path}")

        # Log full command for debugging (to stderr so user can see it)
        print(f"  Command: {' '.join(cmd)}", file=sys.stderr)
        logger.debug(f"Full command: {cmd}")

        # Run opencode
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        # Log stderr if present (for debugging)
        if result.stderr:
            logger.debug(f"OpenCode stderr: {result.stderr}")
            print(f"  OpenCode stderr: {result.stderr[:500]}", file=sys.stderr)

        # Check for errors
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise OpenCodeError(f"OpenCode exited with code {result.returncode}: {error_msg}")

        output = result.stdout.strip()
        if not output:
            # Log more details for debugging
            stdout_len = len(result.stdout) if result.stdout else 0
            stderr_preview = result.stderr[:500] if result.stderr else "(empty)"
            print(f"  OpenCode exit code: {result.returncode}", file=sys.stderr)
            print(f"  OpenCode stdout length: {stdout_len}", file=sys.stderr)
            print(f"  OpenCode stderr: {stderr_preview}", file=sys.stderr)
            raise OpenCodeError("OpenCode returned empty output")

        logger.info(f"OpenCode analysis complete ({len(output)} chars)")
        return output

    except subprocess.TimeoutExpired as e:
        raise OpenCodeTimeoutError(f"OpenCode execution timed out after {timeout} seconds") from e

    finally:
        # Clean up temp files in repo directory
        for tmp_path in [patch_path, prompt_path]:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass


def run_opencode_agent(
    repo_path: Path,
    agent: str,
    prompt: str,
    review_id: int,
    model: str | None = None,
    timeout: int = 300,
    binary_path: str = "opencode",
    patch_file: Path | None = None,
) -> str:
    """Run an opencode custom agent (e.g., api-reviewer).

    Args:
        repo_path: Path to the repository to run opencode in.
        agent: The agent to use (e.g., "api-reviewer").
        prompt: The prompt/message for the agent. Use @filename to attach files.
        review_id: Review Board request ID (used for session title).
        model: Optional model override.
        timeout: Timeout in seconds for the opencode process.
        binary_path: Path to the opencode binary.
        patch_file: Deprecated, use @filename in prompt instead.

    Returns:
        The output from opencode.

    Raises:
        OpenCodeNotFoundError: If opencode binary is not found.
        OpenCodeTimeoutError: If execution times out.
        OpenCodeError: For other execution errors.
    """
    # Verify binary exists
    opencode_bin = find_opencode_binary(binary_path)
    logger.debug(f"Using opencode binary: {opencode_bin}")

    # Build command
    cmd = [
        opencode_bin,
        "run",
        "--agent",
        agent,
        "--title",
        f"API Review #{review_id}",
    ]

    if model:
        cmd.extend(["--model", model])

    # Add the prompt (with @filename syntax for file attachment)
    cmd.append(prompt)

    logger.info(f"Running opencode agent '{agent}' in {repo_path}")
    # Log full command for debugging - print to stderr so user can see it
    print(f"  Command: {' '.join(cmd)}", file=sys.stderr)

    try:
        # Run opencode
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        # Log stderr if present (for debugging)
        if result.stderr:
            logger.debug(f"OpenCode stderr: {result.stderr}")

        # Check for errors
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise OpenCodeError(f"OpenCode exited with code {result.returncode}: {error_msg}")

        output = result.stdout.strip()
        if not output:
            raise OpenCodeError("OpenCode returned empty output")

        logger.info(f"OpenCode agent complete ({len(output)} chars)")
        return output

    except subprocess.TimeoutExpired as e:
        raise OpenCodeTimeoutError(f"OpenCode execution timed out after {timeout} seconds") from e


def check_opencode_available(binary_path: str = "opencode") -> tuple[bool, str]:
    """Check if opencode is available and working.

    Args:
        binary_path: Path to the opencode binary.

    Returns:
        Tuple of (is_available, message).
    """
    try:
        opencode_bin = find_opencode_binary(binary_path)

        # Try to get version
        result = subprocess.run(
            [opencode_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            version = result.stdout.strip() or "unknown version"
            return True, f"OpenCode available: {version}"
        else:
            return False, f"OpenCode found but --version failed: {result.stderr}"

    except OpenCodeNotFoundError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, "OpenCode --version timed out"
    except Exception as e:
        return False, f"Error checking opencode: {e}"


@dataclass
class ParsedIssue:
    """A parsed issue from OpenCode output."""

    title: str
    file_path: str | None = None
    line_number: int | None = None
    severity: str = "medium"
    issue_type: str = "bug"
    comment: str = ""
    suggestion: str | None = None
    raw_text: str = ""  # Original text block for this issue


@dataclass
class ParsedReview:
    """Result of parsing OpenCode output."""

    issues: list[ParsedIssue] = field(default_factory=list)
    unparsed_text: str = ""  # Text that couldn't be parsed into issues
    summary: str = ""  # Overall summary if found


def parse_opencode_output(output: str) -> ParsedReview:
    """Parse OpenCode output into structured issues.

    Extracts issues following the format:
        ### Issue: <title>
        - **File:** `path/to/file.c`
        - **Line:** <number>
        - **Severity:** low/medium/high/critical
        - **Type:** bug/security/performance/style/architecture
        - **Comment:** <description>
        - **Suggestion:** <optional fix>

    Args:
        output: Raw output from OpenCode.

    Returns:
        ParsedReview with extracted issues and remaining text.
    """
    result = ParsedReview()

    # Try multiple patterns for issue blocks
    # Pattern 1: "### Issue:" format (our preferred format from prompt)
    issue_pattern_1 = re.compile(
        r"###\s*Issue:\s*(.+?)(?=###\s*Issue:|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    # Pattern 2: "**N. Title**" format (common OpenCode natural format)
    # Matches "**1. Something**" or "**2. Another thing**" followed by content
    issue_pattern_2 = re.compile(
        r"\*\*(\d+)\.\s*([^*]+)\*\*\s*(.+?)(?=\*\*\d+\.|\Z)",
        re.DOTALL,
    )

    # Try pattern 1 first (preferred)
    matches = list(issue_pattern_1.finditer(output))
    use_pattern_2 = False

    if not matches:
        # Try pattern 2 (fallback)
        matches = list(issue_pattern_2.finditer(output))
        use_pattern_2 = True

    if not matches:
        # No structured issues found, return everything as unparsed
        result.unparsed_text = output.strip()
        return result

    # Track which parts of the output we've parsed
    parsed_ranges: list[tuple[int, int]] = []

    for match in matches:
        issue_text = match.group(0)
        parsed_ranges.append((match.start(), match.end()))

        if use_pattern_2:
            # Pattern 2: groups are (number, title, content)
            title = match.group(2).strip()
            issue_content = match.group(3)
        else:
            # Pattern 1: group is full content including title
            issue_content = match.group(1)
            # Extract title (first line after "### Issue:")
            title_match = re.match(r"([^\n]+)", issue_content.strip())
            title = title_match.group(1).strip() if title_match else "Untitled Issue"

        issue = ParsedIssue(title=title, raw_text=issue_text.strip())

        # Extract fields using patterns
        # File: `path` or File: path
        file_match = re.search(
            r"\*\*File:\*\*\s*`?([^`\n]+)`?",
            issue_content,
            re.IGNORECASE,
        )
        if file_match:
            issue.file_path = file_match.group(1).strip()

        # Line: number (may include descriptive text like "New Code (approx. line 57...)")
        line_match = re.search(
            r"\*\*Line:\*\*\s*(?:.*?(?:line\s*)?)?(\d+)",
            issue_content,
            re.IGNORECASE,
        )
        if line_match:
            try:
                issue.line_number = int(line_match.group(1))
            except ValueError:
                pass

        # Severity: low/medium/high/critical
        severity_match = re.search(
            r"\*\*Severity:\*\*\s*(low|medium|high|critical)",
            issue_content,
            re.IGNORECASE,
        )
        if severity_match:
            issue.severity = severity_match.group(1).lower()

        # Type: bug/security/performance/style/architecture
        type_match = re.search(
            r"\*\*Type:\*\*\s*(\w+)",
            issue_content,
            re.IGNORECASE,
        )
        if type_match:
            issue.issue_type = type_match.group(1).lower()

        # Comment: description (can be multiline until next field or end)
        comment_match = re.search(
            r"\*\*Comment:\*\*\s*(.+?)(?=\n-\s*\*\*|\Z)",
            issue_content,
            re.DOTALL | re.IGNORECASE,
        )
        if comment_match:
            issue.comment = comment_match.group(1).strip()

        # Suggestion: optional fix
        suggestion_match = re.search(
            r"\*\*Suggestion:\*\*\s*(.+?)(?=\n-\s*\*\*|\n###|\Z)",
            issue_content,
            re.DOTALL | re.IGNORECASE,
        )
        if suggestion_match:
            suggestion = suggestion_match.group(1).strip()
            if suggestion and suggestion.lower() not in ("none", "n/a", "-"):
                issue.suggestion = suggestion

        result.issues.append(issue)

    # Build unparsed text from parts not matched
    unparsed_parts = []
    last_end = 0
    for start, end in sorted(parsed_ranges):
        if start > last_end:
            part = output[last_end:start].strip()
            if part:
                unparsed_parts.append(part)
        last_end = end

    # Add any remaining text after the last issue
    if last_end < len(output):
        part = output[last_end:].strip()
        if part:
            unparsed_parts.append(part)

    result.unparsed_text = "\n\n".join(unparsed_parts)

    # Try to extract summary from unparsed text
    summary_match = re.search(
        r"(?:summary|overall|conclusion)[:\s]*(.+?)(?=\n\n|\Z)",
        result.unparsed_text,
        re.DOTALL | re.IGNORECASE,
    )
    if summary_match:
        result.summary = summary_match.group(1).strip()

    return result
