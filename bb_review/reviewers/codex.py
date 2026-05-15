"""Codex CLI runner for code review."""

import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


logger = logging.getLogger(__name__)


class CodexError(Exception):
    """Error running Codex CLI."""

    pass


class CodexNotFoundError(CodexError):
    """Codex binary not found."""

    pass


class CodexTimeoutError(CodexError):
    """Codex execution timed out."""

    pass


def find_codex_binary(binary_path: str = "codex") -> str:
    """Find the codex binary, raising if not found."""
    if Path(binary_path).is_absolute():
        if Path(binary_path).exists():
            return binary_path
        raise CodexNotFoundError(f"Codex binary not found at: {binary_path}")

    resolved = shutil.which(binary_path)
    if resolved:
        return resolved

    raise CodexNotFoundError(
        f"Codex binary '{binary_path}' not found in PATH. Install it with: npm install -g @openai/codex"
    )


def check_codex_available(binary_path: str = "codex") -> tuple[bool, str]:
    """Check if codex CLI is available."""
    try:
        codex_bin = find_codex_binary(binary_path)

        result = subprocess.run(
            [codex_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            version = result.stdout.strip() or "unknown version"
            return True, f"Codex available: {version}"
        else:
            return False, f"Codex found but --version failed: {result.stderr}"

    except CodexNotFoundError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, "Codex --version timed out"
    except Exception as e:
        return False, f"Error checking Codex: {e}"


def build_review_prompt(
    repo_name: str,
    review_id: int,
    summary: str,
    guidelines_context: str,
    focus_areas: list[str],
    at_reviewed_state: bool = False,
    changed_files: list[str] | None = None,
    verbose: bool = False,
    skill_files: list[str] | None = None,
) -> str:
    """Build the review prompt for Codex."""
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

    if skill_files:
        files_list = "\n".join(f"- Read `{f}`" for f in skill_files)
        prompt += f"""
IMPORTANT: This repository has project-specific review guides deployed in the working \
directory. You MUST read them before starting your review:
{files_list}

These contain project-specific conventions, technical patterns, false positive rules, \
and subsystem-specific guidance. Follow them strictly.
"""
    elif guidelines_context:
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

Do not suggest changes outside the scope of the review.

After all ### Issue blocks, end with a standalone summary separated by ---:

---

**Summary:** <1-2 sentence overview of the code quality>

Do NOT put **Summary:** inside any ### Issue block.
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


def build_series_review_prompt(
    repo_name: str,
    reviews: list,
    base_ref: str,
    guidelines_context: str,
    focus_areas: list[str],
    verbose: bool = False,
    skill_files: list[str] | None = None,
) -> str:
    """Build a prompt for reviewing an entire patch series as one unit."""
    focus_str = ", ".join(focus_areas) if focus_areas else "bugs, security, performance"

    patches_list = []
    for r in reviews:
        entry = f"- r/{r.review_request_id}: {r.summary}"
        if r.description:
            desc_lines = r.description.strip().split("\n")
            entry += "\n" + "\n".join(f"  {line}" for line in desc_lines[:5])
        patches_list.append(entry)
    patches_text = "\n".join(patches_list)

    prompt = f"""You are reviewing a patch series ({len(reviews)} commits) applied to \
repository {repo_name}.

All patches have been applied as commits on top of {base_ref}.

Patches in order:
{patches_text}

To review effectively:
1. Run `git log --oneline {base_ref}..HEAD` to see the commit history
2. Run `git diff {base_ref}..HEAD` to see the full combined diff
3. For per-patch diffs, use `git log -p {base_ref}..HEAD`
4. Read the changed files directly for full context and correct line numbers
5. Line numbers in your findings must match the actual file line numbers
"""

    if skill_files:
        files_list = "\n".join(f"- Read `{f}`" for f in skill_files)
        prompt += f"""
IMPORTANT: This repository has project-specific review guides deployed in the working \
directory. You MUST read them before starting your review:
{files_list}

These contain project-specific conventions, technical patterns, false positive rules, \
and subsystem-specific guidance. Follow them strictly.
"""
    elif guidelines_context:
        prompt += f"""
Guidelines:
{guidelines_context}
"""

    prompt += f"""
Focus areas: {focus_str}

Review this as a cohesive patch series. Look for:
- Bugs and logic errors within and across patches
- Security vulnerabilities
- Performance issues
- Cross-patch interactions and consistency
- Architectural coherence of the series as a whole

For each issue found, use this format:

### Issue: <brief title>
- **File:** `path/to/file.c`
- **Line:** <actual line number in the file>
- **Severity:** low/medium/high/critical
- **Type:** bug/security/performance/style/architecture
- **Comment:** <description of the issue>
- **Suggestion:** <optional suggested fix>

For general observations that don't apply to a specific line, omit the Line field.

Do not suggest changes outside the scope of the review.

After all ### Issue blocks, end with a standalone summary separated by ---:

---

**Summary:** <1-2 sentence overview of the series quality>

Do NOT put **Summary:** inside any ### Issue block.
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


def run_codex_review(
    repo_path: Path,
    patch_content: str,
    prompt: str,
    model: str | None = None,
    timeout: int = 300,
    binary_path: str = "codex",
    sandbox: str = "read-only",
    at_reviewed_state: bool = False,
) -> str:
    """Run Codex CLI and return the analysis.

    Args:
        repo_path: Path to the repository.
        patch_content: The raw diff/patch content.
        prompt: The review prompt.
        model: Optional model override (e.g., "o3", "gpt-4.1").
        timeout: Timeout in seconds.
        binary_path: Path to the codex binary.
        sandbox: Sandbox mode (read-only, workspace-write).
        at_reviewed_state: If True, changes are staged - don't write patch file.

    Returns:
        The analysis text from Codex.

    Raises:
        CodexNotFoundError: If binary is not found.
        CodexTimeoutError: If execution times out.
        CodexError: For other execution errors.
    """
    codex_bin = find_codex_binary(binary_path)
    logger.debug(f"Using Codex binary: {codex_bin}")

    # Write patch file if not at reviewed state
    patch_path = None
    if not at_reviewed_state:
        patch_path = repo_path / ".bb_review_patch.diff"
        patch_path.write_text(patch_content)

    # Temp file for capturing the last agent message
    fd, output_path = tempfile.mkstemp(suffix=".txt", prefix="bb_review_codex_")
    os.close(fd)

    try:
        cmd = [
            codex_bin,
            "exec",
            "-s",
            sandbox,
            "-o",
            output_path,
        ]

        if model:
            cmd.extend(["-m", model])

        # Prompt is read from stdin
        cmd.append("-")

        logger.info(f"Running Codex in {repo_path}")
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
            logger.debug(f"Codex stderr: {result.stderr}")
            print(f"  Codex stderr: {result.stderr[:500]}", file=sys.stderr)

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise CodexError(f"Codex exited with code {result.returncode}: {error_msg}")

        # Read output from -o file (last agent message, plain text)
        output_file = Path(output_path)
        if output_file.exists() and output_file.stat().st_size > 0:
            output = output_file.read_text().strip()
        else:
            # Fall back to stdout if -o file is empty
            output = result.stdout.strip()

        if not output:
            raise CodexError("Codex returned empty output")

        logger.info(f"Codex analysis complete ({len(output)} chars)")
        return output

    except subprocess.TimeoutExpired as e:
        raise CodexTimeoutError(f"Codex execution timed out after {timeout} seconds") from e

    finally:
        if patch_path and patch_path.exists():
            try:
                patch_path.unlink()
            except Exception:
                pass
        try:
            Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass
