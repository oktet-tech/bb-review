"""Tests for review guidelines loading and validation."""

from pathlib import Path

import pytest

from bb_review.guidelines import (
    create_example_guidelines,
    load_guidelines,
    validate_guidelines,
)
from bb_review.models import ReviewFocus, ReviewGuidelines, Severity


class TestLoadGuidelines:
    """Tests for load_guidelines function."""

    def test_load_from_yaml(self, tmp_path: Path):
        """Load .ai-review.yaml file."""
        guidelines_content = """
focus:
  - bugs
  - security
  - performance
context: "This is a C project for embedded systems."
severity_threshold: high
custom_rules:
  - "Check for memory leaks"
  - "Verify interrupt safety"
ignore_paths:
  - "tests/*"
  - "*.test.c"
"""
        guidelines_file = tmp_path / ".ai-review.yaml"
        guidelines_file.write_text(guidelines_content)

        guidelines = load_guidelines(tmp_path)

        assert ReviewFocus.BUGS in guidelines.focus
        assert ReviewFocus.SECURITY in guidelines.focus
        assert ReviewFocus.PERFORMANCE in guidelines.focus
        assert "embedded systems" in guidelines.context
        assert guidelines.severity_threshold == Severity.HIGH
        assert len(guidelines.custom_rules) == 2
        assert "tests/*" in guidelines.ignore_paths

    def test_default_guidelines(self, tmp_path: Path):
        """Default values when file missing."""
        # No .ai-review.yaml file
        guidelines = load_guidelines(tmp_path)

        assert guidelines.focus == [ReviewFocus.BUGS, ReviewFocus.SECURITY]
        assert guidelines.severity_threshold == Severity.MEDIUM
        assert guidelines.context == ""
        assert guidelines.custom_rules == []
        assert guidelines.ignore_paths == []

    def test_partial_guidelines(self, tmp_path: Path):
        """Load partial guidelines with defaults for missing fields."""
        guidelines_content = """
focus:
  - bugs
context: "Test project"
"""
        guidelines_file = tmp_path / ".ai-review.yaml"
        guidelines_file.write_text(guidelines_content)

        guidelines = load_guidelines(tmp_path)

        assert guidelines.focus == [ReviewFocus.BUGS]
        assert guidelines.context == "Test project"
        # Defaults for missing fields
        assert guidelines.severity_threshold == Severity.MEDIUM
        assert guidelines.custom_rules == []

    def test_load_empty_file(self, tmp_path: Path):
        """Handle empty .ai-review.yaml file."""
        guidelines_file = tmp_path / ".ai-review.yaml"
        guidelines_file.write_text("")

        guidelines = load_guidelines(tmp_path)

        # Should return defaults
        assert guidelines.focus == [ReviewFocus.BUGS, ReviewFocus.SECURITY]

    def test_load_invalid_yaml(self, tmp_path: Path):
        """Handle invalid YAML syntax."""
        guidelines_file = tmp_path / ".ai-review.yaml"
        guidelines_file.write_text("this is: [not: valid: yaml")

        # Should return defaults on parse error
        guidelines = load_guidelines(tmp_path)
        assert guidelines.focus == [ReviewFocus.BUGS, ReviewFocus.SECURITY]


class TestValidateGuidelines:
    """Tests for validate_guidelines function."""

    def test_validate_unknown_focus(self):
        """Warn on unknown focus area (handled at load time)."""
        # Valid guidelines should produce no warnings
        guidelines = ReviewGuidelines(
            focus=[ReviewFocus.BUGS, ReviewFocus.SECURITY],
        )

        warnings = validate_guidelines(guidelines)
        assert len(warnings) == 0

    def test_validate_empty_focus(self):
        """Warn when focus is empty."""
        guidelines = ReviewGuidelines(focus=[])

        warnings = validate_guidelines(guidelines)
        assert any("focus" in w.lower() for w in warnings)

    def test_validate_valid_guidelines(self):
        """No warnings for valid guidelines."""
        guidelines = ReviewGuidelines(
            focus=[ReviewFocus.BUGS],
            context="Test context",
            severity_threshold=Severity.MEDIUM,
        )

        warnings = validate_guidelines(guidelines)
        assert len(warnings) == 0

    def test_validate_all_focus_areas(self):
        """All focus areas is valid."""
        guidelines = ReviewGuidelines(
            focus=[
                ReviewFocus.BUGS,
                ReviewFocus.SECURITY,
                ReviewFocus.PERFORMANCE,
                ReviewFocus.STYLE,
                ReviewFocus.ARCHITECTURE,
            ],
        )

        warnings = validate_guidelines(guidelines)
        assert len(warnings) == 0


class TestCreateExampleGuidelines:
    """Tests for create_example_guidelines function."""

    def test_create_example(self, tmp_path: Path):
        """Create example .ai-review.yaml."""
        path = create_example_guidelines(tmp_path)

        assert path.exists()
        assert path.name == ".ai-review.yaml"
        content = path.read_text()
        assert "focus" in content

    def test_no_overwrite_by_default(self, tmp_path: Path):
        """Don't overwrite existing file by default."""
        existing = tmp_path / ".ai-review.yaml"
        existing.write_text("# Existing file")

        # Should raise error when file exists
        with pytest.raises(FileExistsError, match="already exists"):
            create_example_guidelines(tmp_path, overwrite=False)

        # Original file should be unchanged
        assert "# Existing file" in existing.read_text()

    def test_overwrite_when_requested(self, tmp_path: Path):
        """Overwrite existing file when requested."""
        existing = tmp_path / ".ai-review.yaml"
        existing.write_text("# Existing file")

        path = create_example_guidelines(tmp_path, overwrite=True)

        assert path.exists()
        # Content should be replaced
        assert "# Existing file" not in path.read_text()
        assert "focus" in path.read_text()


class TestReviewGuidelinesModel:
    """Tests for ReviewGuidelines dataclass."""

    def test_default_factory(self):
        """Test default() class method."""
        guidelines = ReviewGuidelines.default()

        assert guidelines.focus == [ReviewFocus.BUGS, ReviewFocus.SECURITY]
        assert guidelines.severity_threshold == Severity.MEDIUM

    def test_custom_values(self):
        """Create with custom values."""
        guidelines = ReviewGuidelines(
            focus=[ReviewFocus.PERFORMANCE],
            context="Custom context",
            severity_threshold=Severity.HIGH,
            custom_rules=["Rule 1", "Rule 2"],
            ignore_paths=["*.tmp"],
        )

        assert guidelines.focus == [ReviewFocus.PERFORMANCE]
        assert guidelines.context == "Custom context"
        assert guidelines.severity_threshold == Severity.HIGH
        assert len(guidelines.custom_rules) == 2
        assert "*.tmp" in guidelines.ignore_paths
