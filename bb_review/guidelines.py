"""Repository guidelines loader from .ai-review.yaml files."""

import logging
from pathlib import Path
import re
from typing import Any

import yaml

from .models import ReviewFocus, ReviewGuidelines, Severity


logger = logging.getLogger(__name__)

# Root of the bb_review project (for guides/ directory)
_PROJECT_ROOT = Path(__file__).parent.parent

# Default filename for guidelines
GUIDELINES_FILENAME = ".ai-review.yaml"

# Alternative filenames to check
GUIDELINES_ALTERNATIVES = [
    ".ai-review.yml",
    ".ai-review.json",
    "ai-review.yaml",
    "ai-review.yml",
]


def load_guidelines(
    repo_path: Path,
    repo_name: str | None = None,
    changed_files: list[str] | None = None,
    skip_rich_context: bool = False,
) -> ReviewGuidelines:
    """Load review guidelines from a repository.

    Search order:
    1. .ai-review.yaml (or alternatives) in the repository root
    2. guides/{repo_name}.ai-review.yaml in the bb_review project dir

    If a rich guide directory exists at guides/{repo_name}/, its content
    (technical-patterns.md, subsystem guides, etc.) is appended to context.
    Set skip_rich_context=True when the agent reads skill files directly.

    Args:
        repo_path: Path to repository root.
        repo_name: Repository name for guides/ fallback lookup.
        changed_files: File paths from the diff, used for subsystem matching.

    Returns:
        ReviewGuidelines instance.
    """
    # Try primary filename first
    guidelines_path = repo_path / GUIDELINES_FILENAME

    if not guidelines_path.exists():
        # Try alternatives
        for alt_name in GUIDELINES_ALTERNATIVES:
            alt_path = repo_path / alt_name
            if alt_path.exists():
                guidelines_path = alt_path
                break
        else:
            # Try guides/ directory in the bb_review project
            if repo_name:
                guides_dir = _PROJECT_ROOT / "guides"
                guide_file = guides_dir / f"{repo_name}.ai-review.yaml"
                if guide_file.exists():
                    guidelines_path = guide_file
                else:
                    logger.debug(f"No guidelines file found in {repo_path} or guides/, using defaults")
                    guidelines = ReviewGuidelines.default()
                    if skip_rich_context:
                        return guidelines
                    return _enrich_with_rich_context(guidelines, repo_name, changed_files)
            else:
                logger.debug(f"No guidelines file found in {repo_path}, using defaults")
                return ReviewGuidelines.default()

    logger.info(f"Loading guidelines from {guidelines_path}")

    try:
        with open(guidelines_path) as f:
            raw = yaml.safe_load(f)

        if raw is None:
            logger.warning(f"Empty guidelines file: {guidelines_path}")
            guidelines = ReviewGuidelines.default()
        else:
            guidelines = parse_guidelines(raw)

        if skip_rich_context:
            return guidelines
        return _enrich_with_rich_context(guidelines, repo_name, changed_files)

    except yaml.YAMLError as e:
        logger.error(f"Failed to parse guidelines YAML: {e}")
        return ReviewGuidelines.default()
    except Exception as e:
        logger.error(f"Failed to load guidelines: {e}")
        return ReviewGuidelines.default()


def _enrich_with_rich_context(
    guidelines: ReviewGuidelines,
    repo_name: str | None,
    changed_files: list[str] | None,
) -> ReviewGuidelines:
    """Append rich guide content to guidelines context if available."""
    if not repo_name:
        return guidelines

    rich_context = load_rich_context(repo_name, changed_files)
    if not rich_context:
        return guidelines

    logger.info(f"Loaded rich guide context for {repo_name} ({len(rich_context)} chars)")

    if guidelines.context:
        guidelines.context = guidelines.context.rstrip() + "\n\n---\n\n" + rich_context
    else:
        guidelines.context = rich_context

    return guidelines


def parse_guidelines(raw: dict[str, Any]) -> ReviewGuidelines:
    """Parse raw YAML dict into ReviewGuidelines.

    Args:
        raw: Raw parsed YAML dictionary.

    Returns:
        ReviewGuidelines instance.
    """
    # Parse focus areas
    focus = []
    raw_focus = raw.get("focus", ["bugs", "security"])
    for f in raw_focus:
        try:
            focus.append(ReviewFocus(f))
        except ValueError:
            logger.warning(f"Unknown focus area: {f}")

    if not focus:
        focus = [ReviewFocus.BUGS, ReviewFocus.SECURITY]

    # Parse severity threshold
    raw_severity = raw.get("severity_threshold", "medium")
    try:
        severity = Severity(raw_severity)
    except ValueError:
        logger.warning(f"Unknown severity: {raw_severity}, using medium")
        severity = Severity.MEDIUM

    # Parse other fields
    context = raw.get("context", "")
    ignore_paths = raw.get("ignore_paths", [])
    custom_rules = raw.get("custom_rules", [])

    # Ensure lists are actually lists
    if isinstance(ignore_paths, str):
        ignore_paths = [ignore_paths]
    if isinstance(custom_rules, str):
        custom_rules = [custom_rules]

    return ReviewGuidelines(
        focus=focus,
        context=context,
        ignore_paths=ignore_paths,
        severity_threshold=severity,
        custom_rules=custom_rules,
    )


def create_example_guidelines(repo_path: Path, overwrite: bool = False) -> Path:
    """Create an example .ai-review.yaml file.

    Args:
        repo_path: Path to repository root.
        overwrite: Whether to overwrite existing file.

    Returns:
        Path to created file.

    Raises:
        FileExistsError: If file exists and overwrite is False.
    """
    guidelines_path = repo_path / GUIDELINES_FILENAME

    if guidelines_path.exists() and not overwrite:
        raise FileExistsError(f"Guidelines file already exists: {guidelines_path}")

    example_content = """\
# AI Review Guidelines
# This file configures how the AI reviewer analyzes code in this repository.

# Focus areas - what the AI should look for
# Options: bugs, security, performance, style, architecture
focus:
  - bugs          # Logic errors, null checks, edge cases
  - security      # SQL injection, XSS, auth issues, data exposure
  # - performance   # Uncomment to check for performance issues
  # - style         # Uncomment to check coding style
  # - architecture  # Uncomment to check architectural concerns

# Repository-specific context
# Help the AI understand your codebase
context: |
  Describe your project here. For example:
  - What language/framework is this?
  - Any specific patterns or conventions used?
  - Critical areas that need extra attention?

# Paths to ignore during review
# Uses glob patterns
ignore_paths:
  - vendor/
  - node_modules/
  - "*.min.js"
  - "*.generated.*"
  - dist/
  - build/

# Minimum severity to report
# Options: low, medium, high, critical
severity_threshold: medium

# Custom rules or guidelines
# Add any project-specific rules
custom_rules:
  # - "All API endpoints must validate input parameters"
  # - "Database queries must use parameterized statements"
  # - "Error messages must not expose internal details"
"""

    guidelines_path.write_text(example_content)
    logger.info(f"Created example guidelines at {guidelines_path}")

    return guidelines_path


def validate_guidelines(guidelines: ReviewGuidelines) -> list[str]:
    """Validate guidelines and return any warnings.

    Args:
        guidelines: Guidelines to validate.

    Returns:
        List of warning messages.
    """
    warnings = []

    if not guidelines.focus:
        warnings.append("No focus areas specified, will use defaults (bugs, security)")

    if guidelines.severity_threshold == Severity.LOW:
        warnings.append(
            "Severity threshold is 'low' - this may generate many comments. "
            "Consider using 'medium' or higher."
        )

    if len(guidelines.custom_rules) > 10:
        warnings.append(
            f"Many custom rules ({len(guidelines.custom_rules)}) may slow down review. "
            "Consider consolidating."
        )

    return warnings


def get_guides_dir(repo_name: str) -> Path | None:
    """Return the guides/{repo}/ directory if it exists."""
    guides_dir = _PROJECT_ROOT / "guides" / repo_name
    if guides_dir.is_dir():
        return guides_dir
    return None


def load_rich_context(
    repo_name: str,
    changed_files: list[str] | None = None,
) -> str:
    """Load rich review context from guides/{repo}/ directory.

    Reads technical-patterns.md, false-positive-guide.md, and matching
    subsystem guides. Returns concatenated markdown content to inject
    into the review prompt context.

    Args:
        repo_name: Repository name matching guides/{repo}/ dir.
        changed_files: List of file paths from the diff, used to match
            subsystem triggers. If None, no subsystem guides are loaded.

    Returns:
        Concatenated markdown content, or empty string if no guide dir.
    """
    guides_dir = get_guides_dir(repo_name)
    if guides_dir is None:
        return ""

    parts: list[str] = []

    # Load technical patterns (always)
    patterns_path = guides_dir / "technical-patterns.md"
    if patterns_path.exists():
        parts.append(patterns_path.read_text().strip())

    # Load false positive guide (if present)
    fp_path = guides_dir / "false-positive-guide.md"
    if fp_path.exists():
        parts.append(fp_path.read_text().strip())

    # Load matching subsystem guides
    if changed_files:
        subsystem_md = guides_dir / "subsystem" / "subsystem.md"
        if subsystem_md.exists():
            triggers = parse_subsystem_triggers(subsystem_md)
            matched_files = match_subsystems(triggers, changed_files)
            for sub_file in matched_files:
                sub_path = guides_dir / "subsystem" / sub_file
                if sub_path.exists():
                    content = sub_path.read_text().strip()
                    if content and not content.startswith("TODO"):
                        parts.append(content)

    return "\n\n---\n\n".join(parts)


def parse_subsystem_triggers(
    subsystem_md: Path,
) -> list[dict[str, str]]:
    """Parse the subsystem.md trigger table.

    Expects a markdown table with columns: Subsystem, Triggers, File.

    Returns:
        List of dicts with keys: subsystem, triggers (comma-separated), file.
    """
    result = []
    text = subsystem_md.read_text()

    # Match table rows: | Name | triggers | file.md |
    row_pattern = re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
        re.MULTILINE,
    )

    for match in row_pattern.finditer(text):
        subsystem = match.group(1).strip()
        triggers = match.group(2).strip()
        file = match.group(3).strip()

        # Skip header and separator rows
        if subsystem in ("Subsystem", "---", "") or triggers.startswith("---"):
            continue
        if not file.endswith(".md"):
            continue

        result.append(
            {
                "subsystem": subsystem,
                "triggers": triggers,
                "file": file,
            }
        )

    return result


def match_subsystems(
    triggers: list[dict[str, str]],
    changed_files: list[str],
) -> list[str]:
    """Match changed files against subsystem triggers.

    Args:
        triggers: Parsed trigger table from parse_subsystem_triggers().
        changed_files: File paths from the diff.

    Returns:
        Deduplicated list of subsystem .md filenames to load.
    """
    matched: list[str] = []
    seen: set[str] = set()

    # Build a single string of all changed file paths for pattern matching
    files_blob = "\n".join(changed_files).lower()

    for entry in triggers:
        trigger_list = [t.strip().lower() for t in entry["triggers"].split(",")]
        for trigger in trigger_list:
            if not trigger:
                continue
            if trigger in files_blob:
                if entry["file"] not in seen:
                    matched.append(entry["file"])
                    seen.add(entry["file"])
                break

    return matched


def merge_with_defaults(
    repo_guidelines: ReviewGuidelines,
    default_focus: list[ReviewFocus],
    default_severity: Severity,
) -> ReviewGuidelines:
    """Merge repository guidelines with global defaults.

    Args:
        repo_guidelines: Repository-specific guidelines.
        default_focus: Default focus areas from global config.
        default_severity: Default severity from global config.

    Returns:
        Merged guidelines.
    """
    # Use repo settings if specified, otherwise use defaults
    focus = repo_guidelines.focus if repo_guidelines.focus else default_focus
    severity = repo_guidelines.severity_threshold or default_severity

    return ReviewGuidelines(
        focus=focus,
        context=repo_guidelines.context,
        ignore_paths=repo_guidelines.ignore_paths,
        severity_threshold=severity,
        custom_rules=repo_guidelines.custom_rules,
    )
