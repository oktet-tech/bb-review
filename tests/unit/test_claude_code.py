"""Tests for Claude Code reviewer module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from bb_review.reviewers.claude_code import (
    ClaudeCodeError,
    ClaudeCodeNotFoundError,
    ClaudeCodeTimeoutError,
    build_review_prompt,
    check_claude_available,
    find_claude_binary,
    run_claude_review,
)


class TestFindClaudeBinary:
    """Tests for find_claude_binary."""

    def test_finds_binary_in_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = find_claude_binary("claude")
            assert result == "/usr/local/bin/claude"

    def test_raises_when_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(ClaudeCodeNotFoundError, match="not found in PATH"):
                find_claude_binary("claude")

    def test_absolute_path_exists(self, tmp_path):
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        result = find_claude_binary(str(binary))
        assert result == str(binary)

    def test_absolute_path_not_found(self):
        with pytest.raises(ClaudeCodeNotFoundError, match="not found at"):
            find_claude_binary("/nonexistent/path/claude")


class TestCheckClaudeAvailable:
    """Tests for check_claude_available."""

    def test_not_found(self):
        available, msg = check_claude_available("nonexistent-binary-xyz")
        assert available is False
        assert "not found" in msg.lower()

    def test_custom_path_not_found(self, tmp_path):
        fake_path = tmp_path / "fake-claude"
        available, msg = check_claude_available(str(fake_path))
        assert available is False

    def test_version_succeeds(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1.0.0"

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            available, msg = check_claude_available("claude")
            assert available is True
            assert "1.0.0" in msg

    def test_version_fails(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "unknown flag"

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            available, msg = check_claude_available("claude")
            assert available is False


class TestBuildReviewPrompt:
    """Tests for build_review_prompt."""

    def test_basic_prompt(self):
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

    def test_prompt_with_guidelines(self):
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="This is a C project.",
            focus_areas=["bugs"],
        )
        assert "C project" in prompt

    def test_prompt_at_reviewed_state(self):
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
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="",
            focus_areas=["bugs"],
            at_reviewed_state=False,
        )
        assert ".bb_review_patch.diff" in prompt

    def test_prompt_contains_issue_format(self):
        """Prompt should instruct the reviewer to use ### Issue: format."""
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="",
            focus_areas=["bugs"],
        )
        assert "### Issue:" in prompt


class TestRunClaudeReview:
    """Tests for run_claude_review."""

    def test_successful_review(self, tmp_path):
        """JSON envelope is unwrapped correctly."""
        analysis_text = "### Issue: Bug\n**File:** main.c\n**Line:** 10"
        json_output = json.dumps({"result": analysis_text, "cost": 0.01})

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json_output
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            result = run_claude_review(
                repo_path=tmp_path,
                patch_content="diff content",
                prompt="Review this",
                model="sonnet",
                timeout=60,
                max_turns=5,
                binary_path="claude",
                allowed_tools=["Read", "Grep"],
            )
            assert result == analysis_text

            # Verify command was built correctly
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "/usr/local/bin/claude"
            assert "-p" in cmd
            assert "--output-format" in cmd
            assert "json" in cmd
            assert "--model" in cmd
            assert "sonnet" in cmd
            assert "--max-turns" in cmd
            assert "5" in cmd
            assert "--allowedTools" in cmd
            assert "Read,Grep" in cmd

    def test_patch_file_written_in_fallback(self, tmp_path):
        """When not at_reviewed_state, patch file is written then cleaned up."""
        json_output = json.dumps({"result": "No issues found."})

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json_output
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            run_claude_review(
                repo_path=tmp_path,
                patch_content="diff --git a/foo b/foo\n",
                prompt="Review this",
                at_reviewed_state=False,
            )
            # Patch file should be cleaned up
            assert not (tmp_path / ".bb_review_patch.diff").exists()

    def test_no_patch_file_when_at_reviewed_state(self, tmp_path):
        """When at_reviewed_state, no patch file is written."""
        json_output = json.dumps({"result": "Looks good."})

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json_output
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            run_claude_review(
                repo_path=tmp_path,
                patch_content="diff content",
                prompt="Review this",
                at_reviewed_state=True,
            )
            assert not (tmp_path / ".bb_review_patch.diff").exists()

    def test_empty_result_raises(self, tmp_path):
        json_output = json.dumps({"result": "", "cost": 0})

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json_output
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            with pytest.raises(ClaudeCodeError, match='no "result" field'):
                run_claude_review(
                    repo_path=tmp_path,
                    patch_content="diff",
                    prompt="Review",
                )

    def test_invalid_json_raises(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json at all"
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            with pytest.raises(ClaudeCodeError, match="Failed to parse"):
                run_claude_review(
                    repo_path=tmp_path,
                    patch_content="diff",
                    prompt="Review",
                )

    def test_nonzero_exit_raises(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "something went wrong"

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            with pytest.raises(ClaudeCodeError, match="exited with code 1"):
                run_claude_review(
                    repo_path=tmp_path,
                    patch_content="diff",
                    prompt="Review",
                )

    def test_timeout_raises(self, tmp_path):
        import subprocess

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=60),
            ),
        ):
            with pytest.raises(ClaudeCodeTimeoutError, match="timed out"):
                run_claude_review(
                    repo_path=tmp_path,
                    patch_content="diff",
                    prompt="Review",
                    timeout=60,
                )

    def test_mcp_config_in_command(self, tmp_path):
        """--mcp-config is passed to Claude when mcp_config is set."""
        json_output = json.dumps({"result": "No issues."})
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text('{"mcpServers": {}}')

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json_output
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            run_claude_review(
                repo_path=tmp_path,
                patch_content="diff content",
                prompt="Review this",
                mcp_config=mcp_path,
                at_reviewed_state=True,
            )
            cmd = mock_run.call_args[0][0]
            assert "--mcp-config" in cmd
            assert str(mcp_path) in cmd

    def test_mcp_config_not_added_when_none(self, tmp_path):
        """--mcp-config flag is absent when mcp_config is None."""
        json_output = json.dumps({"result": "No issues."})

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json_output
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            run_claude_review(
                repo_path=tmp_path,
                patch_content="diff content",
                prompt="Review this",
                at_reviewed_state=True,
            )
            cmd = mock_run.call_args[0][0]
            assert "--mcp-config" not in cmd

    def test_empty_stdout_raises(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("subprocess.run", return_value=mock_result),
        ):
            with pytest.raises(ClaudeCodeError, match="empty output"):
                run_claude_review(
                    repo_path=tmp_path,
                    patch_content="diff",
                    prompt="Review",
                )
