"""Tests for OpenCode output parsing."""

from bb_review.reviewers.opencode import (
    build_review_prompt,
    check_opencode_available,
    parse_opencode_output,
)


class TestParseOpenCodeOutput:
    """Tests for parse_opencode_output function."""

    def test_parse_issue_format_1(self, sample_opencode_output: str):
        """Parse ### Issue: format."""
        result = parse_opencode_output(sample_opencode_output)

        assert len(result.issues) >= 2
        assert result.issues[0].title == "Unused variable declaration"

    def test_parse_issue_format_2(self):
        """Parse **1. Title** format."""
        output = """Here are the issues:

**1. Potential buffer overflow**
This is a serious issue where the buffer could overflow.
- **File:** `src/buffer.c`
- **Line:** 42
- **Severity:** high
- **Comment:** The strcpy call doesn't check buffer size.

**2. Missing error handling**
Error handling is absent.
- **File:** `src/main.c`
- **Line:** 100
- **Severity:** medium
- **Comment:** Return value not checked.
"""
        result = parse_opencode_output(output)

        assert len(result.issues) == 2
        assert result.issues[0].title == "Potential buffer overflow"
        assert result.issues[1].title == "Missing error handling"

    def test_parse_with_all_fields(self, sample_opencode_output: str):
        """Extract file, line, severity, type."""
        result = parse_opencode_output(sample_opencode_output)

        # Find the issue with all fields
        issue = result.issues[0]

        assert issue.file_path == "src/main.c"
        assert issue.line_number == 12
        assert issue.severity == "low"
        assert issue.issue_type == "style"

    def test_parse_general_issue(self, sample_opencode_output: str):
        """Issue without line number."""
        result = parse_opencode_output(sample_opencode_output)

        # Find general issue (no line number)
        general_issues = [i for i in result.issues if i.line_number is None]

        assert len(general_issues) >= 1
        assert general_issues[0].title == "General code organization concern"

    def test_parse_no_issues(self):
        """Return unparsed text when no issues."""
        output = """I reviewed the code and it looks fine.
No significant issues were found.

The code follows best practices and is well-structured."""

        result = parse_opencode_output(output)

        assert len(result.issues) == 0
        assert "No significant issues" in result.unparsed_text

    def test_extract_summary(self, sample_opencode_output: str):
        """Find summary in output."""
        result = parse_opencode_output(sample_opencode_output)

        # Should extract summary section
        assert result.summary or result.unparsed_text

    def test_parse_with_suggestion(self):
        """Parse issue with suggestion field."""
        output = """### Issue: Security vulnerability
- **File:** `auth.c`
- **Line:** 50
- **Severity:** critical
- **Type:** security
- **Comment:** Password stored in plaintext.
- **Suggestion:** Use bcrypt or argon2 for password hashing.
"""
        result = parse_opencode_output(output)

        assert len(result.issues) == 1
        assert result.issues[0].suggestion is not None
        assert "bcrypt" in result.issues[0].suggestion

    def test_parse_issue_without_type(self):
        """Parse issue without explicit type."""
        output = """### Issue: Code smell
- **File:** `main.c`
- **Line:** 10
- **Severity:** low
- **Comment:** This could be cleaner.
"""
        result = parse_opencode_output(output)

        assert len(result.issues) == 1
        # Should use default type
        assert result.issues[0].issue_type in ["bug", "bugs"]

    def test_parse_multiple_issues_same_file(self):
        """Parse multiple issues in same file."""
        output = """### Issue: First issue
- **File:** `main.c`
- **Line:** 10
- **Comment:** First problem.

### Issue: Second issue
- **File:** `main.c`
- **Line:** 20
- **Comment:** Second problem.
"""
        result = parse_opencode_output(output)

        assert len(result.issues) == 2
        assert all(i.file_path == "main.c" for i in result.issues)
        assert result.issues[0].line_number == 10
        assert result.issues[1].line_number == 20


class TestBuildReviewPrompt:
    """Tests for build_review_prompt function."""

    def test_basic_prompt(self):
        """Build basic prompt."""
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=42738,
            summary="Add new feature",
            guidelines_context="",
            focus_areas=["bugs", "security"],
        )

        assert "test-repo" in prompt
        assert "42738" in prompt
        assert "Add new feature" in prompt
        assert "bugs" in prompt
        assert "security" in prompt

    def test_prompt_with_guidelines(self):
        """Build prompt with guidelines context."""
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="This is a C project using embedded conventions.",
            focus_areas=["bugs"],
        )

        assert "embedded conventions" in prompt

    def test_prompt_at_reviewed_state(self):
        """Prompt when at reviewed state with changed files."""
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="",
            focus_areas=["bugs"],
            at_reviewed_state=True,
            changed_files=["src/main.c", "src/utils.c"],
        )

        assert "git diff --cached" in prompt
        assert "src/main.c" in prompt
        assert "src/utils.c" in prompt

    def test_prompt_with_patch_file(self):
        """Prompt when using patch file."""
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="",
            focus_areas=["bugs"],
            at_reviewed_state=False,
        )

        assert "patch" in prompt.lower()


class TestCheckOpenCodeAvailable:
    """Tests for check_opencode_available function."""

    def test_opencode_not_found(self):
        """Return False when binary not in PATH."""
        available, msg = check_opencode_available("nonexistent-binary-xyz")

        assert available is False
        assert "not found" in msg.lower()

    def test_check_with_custom_path(self, tmp_path):
        """Check with custom path that doesn't exist."""
        fake_path = tmp_path / "fake-opencode"

        available, msg = check_opencode_available(str(fake_path))

        assert available is False
