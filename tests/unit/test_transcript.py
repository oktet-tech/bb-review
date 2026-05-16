"""Tests for transcript pretty-printer."""

import json
from pathlib import Path

from click.testing import CliRunner

from bb_review.cli import main


class TestTranscriptCommand:
    """Tests for bb-review transcript command."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["transcript", "--help"])
        assert result.exit_code == 0
        assert "Pretty-print" in result.output

    def test_claude_transcript(self, tmp_path: Path):
        """Parse Claude verbose JSON array format."""
        transcript = [
            {
                "type": "system",
                "subtype": "init",
                "model": "claude-sonnet-4",
                "cwd": "/tmp",
                "claude_code_version": "1.0.0",
                "session_id": "abc-123-def",
            },
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4",
                    "content": [
                        {"type": "text", "text": "Here is my review."},
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "/tmp/test.c"},
                        },
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0},
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "num_turns": 3,
                "duration_ms": 5000,
                "total_cost_usd": 0.05,
                "result": "Review complete.",
                "modelUsage": {
                    "claude-sonnet-4": {
                        "inputTokens": 100,
                        "outputTokens": 50,
                        "cacheCreationInputTokens": 0,
                        "cacheReadInputTokens": 0,
                        "costUSD": 0.05,
                    }
                },
            },
        ]

        f = tmp_path / "transcript.json"
        f.write_text(json.dumps(transcript))

        runner = CliRunner()
        result = runner.invoke(main, ["transcript", str(f)])

        assert result.exit_code == 0
        assert "INIT" in result.output
        assert "claude-sonnet-4" in result.output
        assert "ASSISTANT" in result.output
        assert "Here is my review." in result.output
        assert "TOOL: Read" in result.output
        assert "RESULT" in result.output
        assert "turns=3" in result.output
        assert "$0.0500" in result.output

    def test_codex_transcript(self, tmp_path: Path):
        """Parse Codex JSONL event stream format."""
        events = [
            {"type": "thread.started", "thread_id": "abc-123"},
            {"type": "turn.started"},
            {
                "type": "item.started",
                "item": {
                    "type": "command_execution",
                    "command": "git diff --cached",
                    "status": "in_progress",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "git diff --cached",
                    "aggregated_output": "+int x = 42;\n",
                    "exit_code": 0,
                    "status": "completed",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "Found one issue in the code.",
                },
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 200, "output_tokens": 100},
            },
        ]

        f = tmp_path / "transcript.jsonl"
        f.write_text("\n".join(json.dumps(e) for e in events))

        runner = CliRunner()
        result = runner.invoke(main, ["transcript", str(f)])

        assert result.exit_code == 0
        assert "THREAD" in result.output
        assert "EXEC: git diff --cached" in result.output
        assert "EXIT=0" in result.output
        assert "AGENT MESSAGE" in result.output
        assert "Found one issue" in result.output

    def test_opencode_transcript(self, tmp_path: Path):
        """Parse OpenCode text log format."""
        content = """\
=== STDERR (debug logs) ===
[DEBUG] Loading model...
[DEBUG] Running review...

=== STDOUT (review output) ===
### Issue: Buffer overflow
- **File:** src/main.c
- **Line:** 42
"""
        f = tmp_path / "transcript.log"
        f.write_text(content)

        runner = CliRunner()
        result = runner.invoke(main, ["transcript", str(f)])

        assert result.exit_code == 0
        assert "Loading model" in result.output
        assert "Buffer overflow" in result.output

    def test_raw_mode(self, tmp_path: Path):
        """--raw outputs full pretty-printed JSON."""
        data = [{"type": "result", "value": 42}]
        f = tmp_path / "t.json"
        f.write_text(json.dumps(data))

        runner = CliRunner()
        result = runner.invoke(main, ["transcript", "--raw", str(f)])

        assert result.exit_code == 0
        # Should be indented
        assert '  "type": "result"' in result.output

    def test_empty_file(self, tmp_path: Path):
        """Handle empty transcript file."""
        f = tmp_path / "empty.json"
        f.write_text("")

        runner = CliRunner()
        result = runner.invoke(main, ["transcript", str(f)])

        assert result.exit_code == 1
        assert "Empty" in result.output

    def test_nonexistent_file(self):
        """Error on nonexistent file."""
        runner = CliRunner()
        result = runner.invoke(main, ["transcript", "/nonexistent/file.json"])

        assert result.exit_code != 0


class TestGuidelinesDeployment:
    """Tests for guidelines_deploy module."""

    def test_deploy_claude_skills(self, tmp_path: Path):
        """Deploy skill dir, commands, and supporting files for Claude."""
        from bb_review.guidelines_deploy import cleanup_deployed, deploy_agent_skills

        result = deploy_agent_skills(tmp_path, "net-drv-ts", "claude")

        assert result.has_skill
        assert result.skill_name == "net-drv-ts"

        # Skill dir with SKILL.md and supporting files
        skill_dir = tmp_path / ".claude" / "skills" / "net-drv-ts"
        assert skill_dir.is_dir()
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "technical-patterns.md").exists()
        assert (skill_dir / "subsystem" / "subsystem.md").exists()

        # Slash commands in .claude/commands/
        commands_dir = tmp_path / ".claude" / "commands"
        assert (commands_dir / "net-drv-ts-review.md").exists()

        # Cleanup removes skill dir and command files
        cleanup_deployed(result)
        assert not skill_dir.exists()
        for path in result.deployed_files:
            assert not path.exists()

    def test_deploy_codex_flat(self, tmp_path: Path):
        """Deploy skills into .codex/ as flat files."""
        from bb_review.guidelines_deploy import deploy_agent_skills

        result = deploy_agent_skills(tmp_path, "net-drv-ts", "codex")

        assert len(result.deployed_files) > 0
        assert (tmp_path / ".codex").is_dir()

    def test_deploy_nonexistent_repo(self, tmp_path: Path):
        """No files deployed for unknown repo."""
        from bb_review.guidelines_deploy import deploy_agent_skills

        result = deploy_agent_skills(tmp_path, "nonexistent-repo-xyz", "claude")

        assert not result.has_skill
        assert result.deployed_files == []
        assert result.deployed_dirs == []

    def test_deploy_unknown_agent(self, tmp_path: Path):
        """Unknown agent type produces no files."""
        from bb_review.guidelines_deploy import deploy_agent_skills

        result = deploy_agent_skills(tmp_path, "net-drv-ts", "unknown_agent")

        assert not result.has_skill
        assert result.deployed_files == []

    def test_render_skill_claude(self):
        """Claude expansion of skill placeholders."""
        from bb_review.guidelines_deploy import _render_skill

        text = "Read {{GUIDE_DIR}}/technical-patterns.md\nFor review, {{REVIEW_GUIDE}}."
        out = _render_skill(text, "claude", "net-drv-ts", "net-drv-ts-review")

        assert out == (
            "Read ${CLAUDE_SKILL_DIR}/technical-patterns.md\n"
            "For review, invoke the `/net-drv-ts-review` command."
        )

    def test_render_skill_codex(self):
        """Codex expansion of skill placeholders."""
        from bb_review.guidelines_deploy import _render_skill

        text = "Read {{GUIDE_DIR}}/technical-patterns.md\nFor review, {{REVIEW_GUIDE}}."
        out = _render_skill(text, "codex", "net-drv-ts", "net-drv-ts-review")

        assert out == (
            "Read .agents/skills/net-drv-ts/technical-patterns.md\n"
            "For review, read `.agents/skills/net-drv-ts/net-drv-ts-review.md`."
        )

    def test_render_skill_no_review_cmd(self):
        """Falls back gracefully when no review command exists."""
        from bb_review.guidelines_deploy import _render_skill

        out = _render_skill("{{REVIEW_GUIDE}}", "codex", "demo", None)

        assert out == "follow the review protocol"

    def test_find_review_cmd_returns_stem(self, tmp_path: Path):
        """Returns the stem of the slash-command file."""
        from bb_review.guidelines_deploy import _find_review_cmd

        slash_dir = tmp_path / "slash-commands"
        slash_dir.mkdir()
        (slash_dir / "demo-review.md").write_text("protocol")

        assert _find_review_cmd(tmp_path) == "demo-review"

    def test_find_review_cmd_no_dir(self, tmp_path: Path):
        """Returns None when slash-commands/ is absent."""
        from bb_review.guidelines_deploy import _find_review_cmd

        assert _find_review_cmd(tmp_path) is None

    def test_find_review_cmd_empty_dir(self, tmp_path: Path):
        """Returns None when slash-commands/ exists but has no .md files."""
        from bb_review.guidelines_deploy import _find_review_cmd

        (tmp_path / "slash-commands").mkdir()

        assert _find_review_cmd(tmp_path) is None
