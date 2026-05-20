"""Deploy review guide skills/commands into repo checkouts for native agent loading."""

from dataclasses import dataclass, field
import logging
from pathlib import Path
import shutil

from .guidelines import get_guides_dir


logger = logging.getLogger(__name__)


# Skill-aware agents: (skill_root, command_root) relative to repo checkout.
# Claude uses plural; OpenCode native uses singular.
_SKILL_AWARE_PATHS = {
    "claude": (".claude/skills", ".claude/commands"),
    "opencode": (".opencode/skill", ".opencode/command"),
}


@dataclass
class DeployResult:
    """Result of deploying guide files into a repo checkout."""

    skill_name: str | None = None
    deployed_files: list[Path] = field(default_factory=list)
    deployed_dirs: list[Path] = field(default_factory=list)

    @property
    def has_skill(self) -> bool:
        return self.skill_name is not None


def deploy_agent_skills(
    repo_path: Path,
    repo_name: str,
    agent_type: str,
) -> DeployResult:
    """Deploy skills and slash-commands into the repo checkout for native agent loading.

    Claude and OpenCode share the same shape via `_deploy_skill_with_commands`;
    only the destination paths and SKILL.md placeholder resolution differ.
    Codex uses its own layout under `.agents/skills/{repo}/`.
    """
    guides_dir = get_guides_dir(repo_name)
    if guides_dir is None:
        return DeployResult()

    if agent_type in _SKILL_AWARE_PATHS:
        result = _deploy_skill_with_commands(repo_path, guides_dir, repo_name, agent_type)
    elif agent_type == "codex":
        result = _deploy_codex(repo_path, guides_dir, repo_name)
    else:
        logger.warning(f"Unknown agent type for skill deployment: {agent_type}")
        return DeployResult()

    total = len(result.deployed_files) + len(result.deployed_dirs)
    if total:
        logger.info(f"Deployed {total} items for {repo_name} ({agent_type})")

    return result


def cleanup_deployed(result: DeployResult) -> None:
    """Remove deployed skill files and directories from repo checkout."""
    for path in result.deployed_dirs:
        try:
            if path.exists():
                shutil.rmtree(path)
        except Exception:
            pass

    for path in result.deployed_files:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _deploy_skill_with_commands(
    repo_path: Path,
    guides_dir: Path,
    repo_name: str,
    agent: str,
) -> DeployResult:
    """Deploy guides for an agent that supports native skill+command discovery.

    SKILL.md goes to `<skill_root>/{repo}/SKILL.md`, supporting files alongside,
    and slash-command files go to `<command_root>/`.
    """
    skill_root_rel, cmd_root_rel = _SKILL_AWARE_PATHS[agent]
    result = DeployResult()
    review_cmd = _find_review_cmd(guides_dir)

    # Skill directory
    skill_src = guides_dir / "skills"
    if skill_src.is_dir():
        skill_files = list(skill_src.glob("*.md"))
        if skill_files:
            skill_dir = repo_path / skill_root_rel / repo_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            result.deployed_dirs.append(skill_dir)
            result.skill_name = repo_name

            rendered = _render_skill(skill_files[0].read_text(), agent, repo_name, review_cmd)
            (skill_dir / "SKILL.md").write_text(rendered)

            _copy_supporting_files(guides_dir, skill_dir)

    # Slash commands
    slash_src = guides_dir / "slash-commands"
    if slash_src.is_dir():
        commands_dir = repo_path / cmd_root_rel
        commands_dir.mkdir(parents=True, exist_ok=True)
        for md_file in slash_src.glob("*.md"):
            dest = commands_dir / md_file.name
            shutil.copy2(md_file, dest)
            result.deployed_files.append(dest)

    return result


def _deploy_codex(
    repo_path: Path,
    guides_dir: Path,
    repo_name: str,
) -> DeployResult:
    """Deploy guides as a Codex skill under .agents/skills/{repo}/.

    Codex has no slash commands in `codex exec`, so the review protocol is
    deployed as a plain file inside the skill directory; SKILL.md points to it.
    """
    result = DeployResult()

    skill_src = guides_dir / "skills"
    if not skill_src.is_dir():
        return result
    skill_files = list(skill_src.glob("*.md"))
    if not skill_files:
        return result

    review_cmd = _find_review_cmd(guides_dir)

    skill_dir = repo_path / ".agents" / "skills" / repo_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    result.deployed_dirs.append(skill_dir)
    result.skill_name = repo_name

    rendered = _render_skill(skill_files[0].read_text(), "codex", repo_name, review_cmd)
    (skill_dir / "SKILL.md").write_text(rendered)

    # Review protocol as a plain file inside the skill dir. Not tracked in
    # deployed_files -- it lives inside skill_dir, which cleanup removes wholesale.
    slash_src = guides_dir / "slash-commands"
    if slash_src.is_dir():
        for md_file in slash_src.glob("*.md"):
            shutil.copy2(md_file, skill_dir / md_file.name)

    _copy_supporting_files(guides_dir, skill_dir)
    return result


def _copy_supporting_files(guides_dir: Path, skill_dir: Path) -> None:
    """Copy technical-patterns, false-positive-guide, and subsystem/ into skill dir."""
    for filename in ("technical-patterns.md", "false-positive-guide.md"):
        src = guides_dir / filename
        if src.exists():
            shutil.copy2(src, skill_dir / filename)

    subsystem_src = guides_dir / "subsystem"
    if subsystem_src.is_dir():
        subsystem_dest = skill_dir / "subsystem"
        shutil.copytree(subsystem_src, subsystem_dest, dirs_exist_ok=True)


def _find_review_cmd(guides_dir: Path) -> str | None:
    """Return the review command name (stem of the slash-command file)."""
    slash_src = guides_dir / "slash-commands"
    if slash_src.is_dir():
        files = sorted(slash_src.glob("*.md"))
        if files:
            return files[0].stem
    return None


def _render_skill(
    text: str,
    agent: str,
    repo_name: str,
    review_cmd: str | None,
) -> str:
    """Resolve {{GUIDE_DIR}} and {{REVIEW_GUIDE}} placeholders for an agent.

    {{GUIDE_DIR}}    -> the directory holding the skill's supporting files.
    {{REVIEW_GUIDE}} -> how the agent loads the repo-specific review protocol.
    """
    if agent == "claude":
        guide_dir = "${CLAUDE_SKILL_DIR}"
        review_guide = f"invoke the `/{review_cmd}` command" if review_cmd else "follow the review protocol"
    elif agent == "opencode":
        guide_dir = f".opencode/skill/{repo_name}"
        review_guide = f"invoke the `/{review_cmd}` command" if review_cmd else "follow the review protocol"
    else:  # codex
        guide_dir = f".agents/skills/{repo_name}"
        review_guide = (
            f"read `.agents/skills/{repo_name}/{review_cmd}.md`"
            if review_cmd
            else "follow the review protocol"
        )
    return text.replace("{{GUIDE_DIR}}", guide_dir).replace("{{REVIEW_GUIDE}}", review_guide)
