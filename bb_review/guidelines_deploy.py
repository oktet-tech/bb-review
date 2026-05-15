"""Deploy review guide skills/commands into repo checkouts for native agent loading."""

from dataclasses import dataclass, field
import logging
from pathlib import Path
import shutil

from .guidelines import get_guides_dir


logger = logging.getLogger(__name__)


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

    For Claude Code:
      - skills/{repo}.md -> .claude/skills/{repo}/SKILL.md
      - technical-patterns.md, subsystem/ -> .claude/skills/{repo}/
      - slash-commands/*.md -> .claude/commands/

    For Codex/OpenCode: flat copy into .codex/ or .opencode/ (unchanged).

    Returns:
        DeployResult with skill name and paths for cleanup.
    """
    guides_dir = get_guides_dir(repo_name)
    if guides_dir is None:
        return DeployResult()

    if agent_type == "claude":
        result = _deploy_claude(repo_path, guides_dir, repo_name)
    else:
        result = _deploy_flat(repo_path, guides_dir, agent_type)

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


def _deploy_claude(
    repo_path: Path,
    guides_dir: Path,
    repo_name: str,
) -> DeployResult:
    """Deploy guides using Claude Code's native skill/command structure.

    Skills go to .claude/skills/{repo}/ with supporting files alongside.
    Slash-commands go to .claude/commands/.
    """
    result = DeployResult()

    # --- Skill directory: .claude/skills/{repo}/ ---
    skill_src = guides_dir / "skills"
    if skill_src.is_dir():
        # Find the skill file (expect exactly one per repo)
        skill_files = list(skill_src.glob("*.md"))
        if skill_files:
            skill_dir = repo_path / ".claude" / "skills" / repo_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            result.deployed_dirs.append(skill_dir)
            result.skill_name = repo_name

            # Copy skill file as SKILL.md
            shutil.copy2(skill_files[0], skill_dir / "SKILL.md")

            # Copy supporting files into skill dir
            _copy_supporting_files(guides_dir, skill_dir)

    # --- Slash commands: .claude/commands/ ---
    slash_src = guides_dir / "slash-commands"
    if slash_src.is_dir():
        commands_dir = repo_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        for md_file in slash_src.glob("*.md"):
            dest = commands_dir / md_file.name
            shutil.copy2(md_file, dest)
            result.deployed_files.append(dest)

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


# --- Flat deploy for Codex/OpenCode (unchanged, deferred) ---

_AGENT_DIRS = {
    "codex": ".codex",
    "opencode": ".opencode",
}


def _deploy_flat(
    repo_path: Path,
    guides_dir: Path,
    agent_type: str,
) -> DeployResult:
    """Flat copy of skills and slash-commands for non-Claude agents."""
    rel = _AGENT_DIRS.get(agent_type)
    if rel is None:
        logger.warning(f"Unknown agent type for skill deployment: {agent_type}")
        return DeployResult()

    dest_dir = repo_path / rel
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = DeployResult()

    for subdir in ("skills", "slash-commands"):
        src = guides_dir / subdir
        if src.is_dir():
            for md_file in src.glob("*.md"):
                dest = dest_dir / md_file.name
                shutil.copy2(md_file, dest)
                result.deployed_files.append(dest)

    for filename in ("technical-patterns.md", "false-positive-guide.md"):
        src = guides_dir / filename
        if src.exists():
            dest = dest_dir / filename
            shutil.copy2(src, dest)
            result.deployed_files.append(dest)

    return result
