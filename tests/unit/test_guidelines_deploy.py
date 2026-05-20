"""Tests for guidelines_deploy: native skill+command deploy for Claude and OpenCode."""

from pathlib import Path

import pytest

from bb_review import guidelines_deploy
from bb_review.guidelines_deploy import cleanup_deployed, deploy_agent_skills


SKILL_BODY = (
    "---\n"
    "name: demo\n"
    "description: Demo skill for tests with sufficient description text.\n"
    "---\n"
    "\n"
    "Read {{GUIDE_DIR}}/technical-patterns.md.\n"
    "For protocol, {{REVIEW_GUIDE}}.\n"
)

REVIEW_CMD_BODY = "# Review protocol\n\nDo the review.\n"
TECH_PATTERNS_BODY = "# Technical patterns\n\nPatterns body.\n"
SUBSYSTEM_INDEX_BODY = "# Subsystem index\n\nIndex body.\n"
SUBSYSTEM_NET_BODY = "# Net subsystem\n\nNet body.\n"


@pytest.fixture
def guides_root(tmp_path, monkeypatch):
    """Build a fake guides/{repo}/ tree and point get_guides_dir at it."""
    repo_name = "demo"
    root = tmp_path / "guides_src"
    repo_guides = root / repo_name
    (repo_guides / "skills").mkdir(parents=True)
    (repo_guides / "slash-commands").mkdir()
    (repo_guides / "subsystem").mkdir()

    (repo_guides / "skills" / f"{repo_name}.md").write_text(SKILL_BODY)
    (repo_guides / "slash-commands" / "demo-review.md").write_text(REVIEW_CMD_BODY)
    (repo_guides / "technical-patterns.md").write_text(TECH_PATTERNS_BODY)
    (repo_guides / "subsystem" / "subsystem.md").write_text(SUBSYSTEM_INDEX_BODY)
    (repo_guides / "subsystem" / "net.md").write_text(SUBSYSTEM_NET_BODY)

    # Patch get_guides_dir to resolve our fake repo
    from bb_review import guidelines_deploy

    def _fake_get_guides_dir(name: str) -> Path | None:
        candidate = root / name
        return candidate if candidate.exists() else None

    monkeypatch.setattr(guidelines_deploy, "get_guides_dir", _fake_get_guides_dir)
    return root


@pytest.fixture
def repo_path(tmp_path):
    """Empty repo checkout to deploy into."""
    p = tmp_path / "repo_checkout"
    p.mkdir()
    return p


class TestDeployClaude:
    def test_skill_directory_and_supporting_files(self, guides_root, repo_path):
        result = deploy_agent_skills(repo_path, "demo", "claude")

        skill_dir = repo_path / ".claude" / "skills" / "demo"
        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "technical-patterns.md").is_file()
        assert (skill_dir / "subsystem" / "subsystem.md").is_file()
        assert (skill_dir / "subsystem" / "net.md").is_file()
        assert result.skill_name == "demo"

    def test_command_file_deployed(self, guides_root, repo_path):
        deploy_agent_skills(repo_path, "demo", "claude")
        assert (repo_path / ".claude" / "commands" / "demo-review.md").is_file()

    def test_placeholder_resolution(self, guides_root, repo_path):
        deploy_agent_skills(repo_path, "demo", "claude")
        rendered = (repo_path / ".claude" / "skills" / "demo" / "SKILL.md").read_text()
        assert "{{GUIDE_DIR}}" not in rendered
        assert "{{REVIEW_GUIDE}}" not in rendered
        assert "Read ${CLAUDE_SKILL_DIR}/technical-patterns.md" in rendered
        assert "invoke the `/demo-review` command" in rendered

    def test_cleanup_removes_everything(self, guides_root, repo_path):
        result = deploy_agent_skills(repo_path, "demo", "claude")
        cleanup_deployed(result)
        assert not (repo_path / ".claude" / "skills" / "demo").exists()
        assert not (repo_path / ".claude" / "commands" / "demo-review.md").exists()


class TestDeployOpenCode:
    def test_skill_directory_and_supporting_files(self, guides_root, repo_path):
        result = deploy_agent_skills(repo_path, "demo", "opencode")

        skill_dir = repo_path / ".opencode" / "skill" / "demo"
        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "technical-patterns.md").is_file()
        assert (skill_dir / "subsystem" / "subsystem.md").is_file()
        assert (skill_dir / "subsystem" / "net.md").is_file()
        assert result.skill_name == "demo"

    def test_command_file_deployed(self, guides_root, repo_path):
        deploy_agent_skills(repo_path, "demo", "opencode")
        assert (repo_path / ".opencode" / "command" / "demo-review.md").is_file()

    def test_placeholder_resolution(self, guides_root, repo_path):
        deploy_agent_skills(repo_path, "demo", "opencode")
        rendered = (repo_path / ".opencode" / "skill" / "demo" / "SKILL.md").read_text()
        assert "{{GUIDE_DIR}}" not in rendered
        assert "{{REVIEW_GUIDE}}" not in rendered
        assert "Read .opencode/skill/demo/technical-patterns.md" in rendered
        assert "invoke the `/demo-review` command" in rendered

    def test_cleanup_removes_everything(self, guides_root, repo_path):
        result = deploy_agent_skills(repo_path, "demo", "opencode")
        cleanup_deployed(result)
        assert not (repo_path / ".opencode" / "skill" / "demo").exists()
        assert not (repo_path / ".opencode" / "command" / "demo-review.md").exists()


class TestDeployMissingGuides:
    def test_returns_empty_result(self, tmp_path, monkeypatch):
        monkeypatch.setattr(guidelines_deploy, "get_guides_dir", lambda _name: None)
        result = deploy_agent_skills(tmp_path, "demo", "opencode")
        assert result.skill_name is None
        assert not result.deployed_files
        assert not result.deployed_dirs
