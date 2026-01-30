"""Repository guidelines loader from .ai-review.yaml files."""

import logging
from pathlib import Path
from typing import Any

import yaml

from .models import ReviewFocus, ReviewGuidelines, Severity


logger = logging.getLogger(__name__)

# Default filename for guidelines
GUIDELINES_FILENAME = ".ai-review.yaml"

# Alternative filenames to check
GUIDELINES_ALTERNATIVES = [
    ".ai-review.yml",
    ".ai-review.json",
    "ai-review.yaml",
    "ai-review.yml",
]


def load_guidelines(repo_path: Path) -> ReviewGuidelines:
    """Load review guidelines from a repository.

    Searches for .ai-review.yaml in the repository root.
    Falls back to defaults if not found.

    Args:
        repo_path: Path to repository root.

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
            logger.debug(f"No guidelines file found in {repo_path}, using defaults")
            return ReviewGuidelines.default()

    logger.info(f"Loading guidelines from {guidelines_path}")

    try:
        with open(guidelines_path) as f:
            raw = yaml.safe_load(f)

        if raw is None:
            logger.warning(f"Empty guidelines file: {guidelines_path}")
            return ReviewGuidelines.default()

        return parse_guidelines(raw)

    except yaml.YAMLError as e:
        logger.error(f"Failed to parse guidelines YAML: {e}")
        return ReviewGuidelines.default()
    except Exception as e:
        logger.error(f"Failed to load guidelines: {e}")
        return ReviewGuidelines.default()


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
