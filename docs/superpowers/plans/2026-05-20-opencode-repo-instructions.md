# OpenCode Repo Instructions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `guides/{repo}/` content to OpenCode via OpenCode's native skill + command discovery (`.opencode/skill/` and `.opencode/command/`), mirroring the existing Claude path.

**Architecture:** Factor the existing Claude skill-deploy logic in `bb_review/guidelines_deploy.py` into a shared helper that also handles OpenCode. Drop the lossy `_deploy_flat` path. Change `reviewers/opencode.py` prompt API from a file list to a `skill_name`, matching Claude. Wire the new `skill_name` through `cli/opencode.py` (single review + series).

**Tech Stack:** Python 3.10+, pytest, ruff. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-20-opencode-repo-instructions-design.md`](../specs/2026-05-20-opencode-repo-instructions-design.md)

---

## File Map

- **Modify** `bb_review/guidelines_deploy.py` — factor shared skill+command deploy helper; add `opencode` branch in `_render_skill`; dispatcher; delete `_deploy_flat` and `_AGENT_DIRS`.
- **Modify** `bb_review/reviewers/opencode.py` — `build_review_prompt` and `build_series_review_prompt`: replace `skill_files: list[str] | None` with `skill_name: str | None`; prompt wording mirrors Claude.
- **Modify** `bb_review/cli/opencode.py` — `run_opencode_for_review` passes `skill_name=deploy_result.skill_name`; `run_opencode_for_series` gains skill deploy + same wiring.
- **Create** `tests/unit/test_guidelines_deploy.py` — covers Claude and OpenCode deploy + cleanup + placeholder resolution.
- **Modify** `tests/unit/test_opencode_parsing.py` — drop `skill_files` test, add `skill_name` test.

---

## Pre-flight

- [ ] **Step 0: Confirm working directory and clean tree**

```bash
pwd
git status
```

Expected: working tree at `/Users/kostik/prj/tools/bb_review`, no uncommitted changes besides the spec doc already committed.

---

## Task 1: Test scaffold for `guidelines_deploy`

**Files:**
- Create: `tests/unit/test_guidelines_deploy.py`

There is no existing test for `guidelines_deploy.py`. Add unit tests now (TDD), driving the refactor in Task 2. Two fixtures: a fake `guides/` source tree, and `tmp_path` as the repo checkout.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_guidelines_deploy.py` with the following content. The tests target the NEW behavior we want — they will fail against the current code (no `.opencode/skill/`, no subsystem copy for OpenCode, `{{GUIDE_DIR}}` unresolved).

```python
"""Tests for guidelines_deploy: native skill+command deploy for Claude and OpenCode."""

from pathlib import Path

import pytest


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
        from bb_review.guidelines_deploy import deploy_agent_skills

        result = deploy_agent_skills(repo_path, "demo", "claude")

        skill_dir = repo_path / ".claude" / "skills" / "demo"
        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "technical-patterns.md").is_file()
        assert (skill_dir / "subsystem" / "subsystem.md").is_file()
        assert (skill_dir / "subsystem" / "net.md").is_file()
        assert result.skill_name == "demo"

    def test_command_file_deployed(self, guides_root, repo_path):
        from bb_review.guidelines_deploy import deploy_agent_skills

        deploy_agent_skills(repo_path, "demo", "claude")
        assert (repo_path / ".claude" / "commands" / "demo-review.md").is_file()

    def test_placeholder_resolution(self, guides_root, repo_path):
        from bb_review.guidelines_deploy import deploy_agent_skills

        deploy_agent_skills(repo_path, "demo", "claude")
        rendered = (repo_path / ".claude" / "skills" / "demo" / "SKILL.md").read_text()
        assert "{{GUIDE_DIR}}" not in rendered
        assert "{{REVIEW_GUIDE}}" not in rendered
        assert "${CLAUDE_SKILL_DIR}" in rendered
        assert "/demo-review" in rendered

    def test_cleanup_removes_everything(self, guides_root, repo_path):
        from bb_review.guidelines_deploy import cleanup_deployed, deploy_agent_skills

        result = deploy_agent_skills(repo_path, "demo", "claude")
        cleanup_deployed(result)
        assert not (repo_path / ".claude" / "skills" / "demo").exists()
        assert not (repo_path / ".claude" / "commands" / "demo-review.md").exists()


class TestDeployOpenCode:
    def test_skill_directory_and_supporting_files(self, guides_root, repo_path):
        from bb_review.guidelines_deploy import deploy_agent_skills

        result = deploy_agent_skills(repo_path, "demo", "opencode")

        skill_dir = repo_path / ".opencode" / "skill" / "demo"
        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "technical-patterns.md").is_file()
        assert (skill_dir / "subsystem" / "subsystem.md").is_file()
        assert (skill_dir / "subsystem" / "net.md").is_file()
        assert result.skill_name == "demo"

    def test_command_file_deployed(self, guides_root, repo_path):
        from bb_review.guidelines_deploy import deploy_agent_skills

        deploy_agent_skills(repo_path, "demo", "opencode")
        assert (repo_path / ".opencode" / "command" / "demo-review.md").is_file()

    def test_placeholder_resolution(self, guides_root, repo_path):
        from bb_review.guidelines_deploy import deploy_agent_skills

        deploy_agent_skills(repo_path, "demo", "opencode")
        rendered = (repo_path / ".opencode" / "skill" / "demo" / "SKILL.md").read_text()
        assert "{{GUIDE_DIR}}" not in rendered
        assert "{{REVIEW_GUIDE}}" not in rendered
        assert ".opencode/skill/demo" in rendered
        assert "/demo-review" in rendered

    def test_cleanup_removes_everything(self, guides_root, repo_path):
        from bb_review.guidelines_deploy import cleanup_deployed, deploy_agent_skills

        result = deploy_agent_skills(repo_path, "demo", "opencode")
        cleanup_deployed(result)
        assert not (repo_path / ".opencode" / "skill" / "demo").exists()
        assert not (repo_path / ".opencode" / "command" / "demo-review.md").exists()


class TestDeployMissingGuides:
    def test_returns_empty_result(self, tmp_path, monkeypatch):
        from bb_review import guidelines_deploy
        from bb_review.guidelines_deploy import deploy_agent_skills

        monkeypatch.setattr(guidelines_deploy, "get_guides_dir", lambda _name: None)
        result = deploy_agent_skills(tmp_path, "demo", "opencode")
        assert result.skill_name is None
        assert not result.deployed_files
        assert not result.deployed_dirs
```

- [ ] **Step 2: Run the new tests; expect failures**

Run: `uv run pytest tests/unit/test_guidelines_deploy.py -v`

Expected: `TestDeployOpenCode::test_skill_directory_and_supporting_files` and the other OpenCode tests FAIL because current code uses `.opencode/` flat without subsystem dir; `TestDeployClaude::test_placeholder_resolution` should PASS already. Don't be surprised if some Claude tests pass — that's the existing behavior. The OpenCode tests are what we're driving.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/test_guidelines_deploy.py
git commit -m "test: add guidelines_deploy tests for Claude+OpenCode native deploy"
```

---

## Task 2: Refactor `guidelines_deploy.py` to share skill+command deploy

**Files:**
- Modify: `bb_review/guidelines_deploy.py` (whole file, see below)

Goal of this task: introduce `_SKILL_AWARE_PATHS`, write `_deploy_skill_with_commands`, extend `_render_skill` with the `opencode` branch, route both `claude` and `opencode` through the shared helper, and delete `_deploy_flat` + `_AGENT_DIRS`. `_deploy_codex` stays unchanged.

- [ ] **Step 1: Apply the rewrite**

Replace the contents of `bb_review/guidelines_deploy.py` with:

```python
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
    "claude":   (".claude/skills",  ".claude/commands"),
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
        review_guide = (
            f"invoke the `/{review_cmd}` command" if review_cmd else "follow the review protocol"
        )
    elif agent == "opencode":
        guide_dir = f".opencode/skill/{repo_name}"
        review_guide = (
            f"invoke the `/{review_cmd}` command" if review_cmd else "follow the review protocol"
        )
    else:  # codex
        guide_dir = f".agents/skills/{repo_name}"
        review_guide = (
            f"read `.agents/skills/{repo_name}/{review_cmd}.md`"
            if review_cmd
            else "follow the review protocol"
        )
    return text.replace("{{GUIDE_DIR}}", guide_dir).replace("{{REVIEW_GUIDE}}", review_guide)
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/unit/test_guidelines_deploy.py -v`

Expected: ALL tests in the file PASS — Claude and OpenCode deploy + cleanup + placeholder resolution.

- [ ] **Step 3: Run the full unit suite to catch regressions**

Run: `uv run pytest tests/unit/ -v`

Expected: All previously-passing tests still pass. (Tests of CLI flows that depend on `build_review_prompt`'s `skill_files` parameter live in `tests/unit/test_opencode_parsing.py` and currently don't reference `skill_files` for OpenCode; they should still pass. Confirm.)

- [ ] **Step 4: Commit**

```bash
git add bb_review/guidelines_deploy.py
git commit -m "refactor: share skill+command deploy between Claude and OpenCode

Add _deploy_skill_with_commands covering both agents; new opencode branch
in _render_skill resolves {{GUIDE_DIR}} to .opencode/skill/{repo} and
{{REVIEW_GUIDE}} to the /{cmd} invocation. Drops the lossy _deploy_flat
path that omitted subsystem/ and left placeholders unresolved."
```

---

## Task 3: Switch `reviewers/opencode.py` prompt API from `skill_files` to `skill_name`

**Files:**
- Modify: `bb_review/reviewers/opencode.py` (two functions)
- Modify: `tests/unit/test_opencode_parsing.py` (replace skill_files test patterns)

- [ ] **Step 1: Add a failing test for the new prompt wording**

Open `tests/unit/test_opencode_parsing.py`. Append inside class `TestBuildReviewPrompt`:

```python
    def test_prompt_with_skill_name(self):
        """When skill_name is set, prompt instructs OpenCode to invoke the skill."""
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="",
            focus_areas=["bugs"],
            skill_name="net-drv-ts",
        )

        assert "/net-drv-ts" in prompt
        # File-by-file listing should be gone when using a skill
        assert ".bb_review_skill_" not in prompt
        assert "Read `.opencode/" not in prompt

    def test_prompt_with_skill_name_skips_inline_guidelines(self):
        """skill_name takes precedence over guidelines_context."""
        prompt = build_review_prompt(
            repo_name="test-repo",
            review_id=1,
            summary="Test",
            guidelines_context="This text should NOT appear when a skill is set.",
            focus_areas=["bugs"],
            skill_name="net-drv-ts",
        )

        assert "This text should NOT appear" not in prompt
        assert "/net-drv-ts" in prompt
```

- [ ] **Step 2: Run the new tests; expect failure**

Run: `uv run pytest tests/unit/test_opencode_parsing.py::TestBuildReviewPrompt -v`

Expected: the two new tests FAIL — `build_review_prompt` doesn't accept `skill_name`.

- [ ] **Step 3: Update `build_review_prompt` in `bb_review/reviewers/opencode.py`**

In `bb_review/reviewers/opencode.py`, change the `build_review_prompt` signature: replace `skill_files: list[str] | None = None` with `skill_name: str | None = None`. Update the body's skill block to mirror the Claude wording.

Locate the current block (around line 125-139):

```python
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
```

Replace it with:

```python
    if skill_name:
        prompt += f"""
IMPORTANT: Before starting your review, invoke the /{skill_name} skill.
It contains project-specific conventions, technical patterns, and subsystem-specific guidance.
Follow it strictly, including any review command or subsystem guides it directs you to.
"""
    elif guidelines_context:
        prompt += f"""
Guidelines:
{guidelines_context}
"""
```

And change the function parameter — find:

```python
    skill_files: list[str] | None = None,
```

Replace with:

```python
    skill_name: str | None = None,
```

Also update the docstring entry for that parameter: replace the `skill_files` line with:

```python
        skill_name: Deployed skill name to invoke (e.g. "net-drv-ts").
```

- [ ] **Step 4: Apply the same change to `build_series_review_prompt`**

Locate the same `if skill_files:` block in `build_series_review_prompt` (around line 239-253) and apply the identical substitution. Update its signature and docstring the same way.

- [ ] **Step 5: Run the prompt tests**

Run: `uv run pytest tests/unit/test_opencode_parsing.py::TestBuildReviewPrompt -v`

Expected: all pass.

- [ ] **Step 6: Run the full unit suite**

Run: `uv run pytest tests/unit/ -v`

Expected: all pass, EXCEPT possibly CLI integration in `bb_review/cli/opencode.py` is now broken because it still passes `skill_files=...`. Unit tests don't import that CLI path at call-time, so this should still be green. If anything fails here, stop and inspect.

- [ ] **Step 7: Commit**

```bash
git add bb_review/reviewers/opencode.py tests/unit/test_opencode_parsing.py
git commit -m "refactor: opencode prompt takes skill_name like Claude

Replace the file-by-file listing with a single /{skill} invocation pointing
at the native OpenCode skill. Mirrors reviewers/claude_code.py."
```

---

## Task 4: Wire `skill_name` into `cli/opencode.py` single-review path

**Files:**
- Modify: `bb_review/cli/opencode.py:236-283` (function `run_opencode_for_review`)

- [ ] **Step 1: Apply the change**

In `bb_review/cli/opencode.py`, locate the block that builds `skill_files` (currently lines ~256-262 and ~282). The current code is:

```python
    # OpenCode uses flat deploy -- convert to file list for prompt
    skill_files = (
        [str(p.relative_to(repo_path)) for p in deploy_result.deployed_files]
        if deploy_result.deployed_files
        else None
    )

    guidelines_context = ""
    if not skill_files:
        if guidelines.context:
            guidelines_context = guidelines.context
        if guidelines.custom_rules:
            if guidelines_context:
                guidelines_context += "\n\nCustom rules:\n"
            guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)
```

Replace with:

```python
    skill_name = deploy_result.skill_name

    guidelines_context = ""
    if not skill_name:
        if guidelines.context:
            guidelines_context = guidelines.context
        if guidelines.custom_rules:
            if guidelines_context:
                guidelines_context += "\n\nCustom rules:\n"
            guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)
```

And in the `build_review_prompt(...)` call just below, replace `skill_files=skill_files,` with `skill_name=skill_name,`.

- [ ] **Step 2: Run unit tests**

Run: `uv run pytest tests/unit/ -v`

Expected: all pass.

- [ ] **Step 3: Run ruff to confirm clean**

Run: `uv run ruff check bb_review/cli/opencode.py`

Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add bb_review/cli/opencode.py
git commit -m "feat: pass skill_name from deploy result to opencode prompt"
```

---

## Task 5: Add skill deploy + `skill_name` to `run_opencode_for_series`

**Files:**
- Modify: `bb_review/cli/opencode.py:355-406` (function `run_opencode_for_series`)

The series path currently calls neither `deploy_agent_skills` nor passes any skill info. Wire it up to match the single-review path.

- [ ] **Step 1: Add a failing assertion via grep**

Run: `grep -n "deploy_agent_skills" bb_review/cli/opencode.py`

Expected: ONE match (in `run_opencode_for_review` only). After this task, expect TWO.

- [ ] **Step 2: Apply the change**

Replace the body of `run_opencode_for_series` (currently `bb_review/cli/opencode.py:355-406`) with:

```python
def run_opencode_for_series(
    reviews: list,
    base_ref: str,
    repo_path: Path,
    repo_config,
    model: str | None,
    timeout: int,
    binary_path: str | None,
    verbose: bool = False,
) -> str:
    """Run OpenCode analysis for an entire patch series."""
    from ..guidelines_deploy import cleanup_deployed, deploy_agent_skills

    deploy_result = deploy_agent_skills(repo_path, repo_config.name, "opencode")

    guidelines = load_guidelines(
        repo_path,
        repo_name=repo_config.name,
        skip_rich_context=deploy_result.has_skill,
    )

    warnings = validate_guidelines(guidelines)
    for warning in warnings:
        click.echo(f"    Warning: {warning}", err=True)

    skill_name = deploy_result.skill_name

    guidelines_context = ""
    if not skill_name:
        if guidelines.context:
            guidelines_context = guidelines.context
        if guidelines.custom_rules:
            if guidelines_context:
                guidelines_context += "\n\nCustom rules:\n"
            guidelines_context += "\n".join(f"- {rule}" for rule in guidelines.custom_rules)

    focus_areas = [f.value for f in guidelines.focus]
    prompt = build_series_review_prompt(
        repo_name=repo_config.name,
        reviews=reviews,
        base_ref=base_ref,
        guidelines_context=guidelines_context,
        focus_areas=focus_areas,
        verbose=verbose,
        skill_name=skill_name,
    )

    click.echo("    Running OpenCode series analysis...")

    try:
        return run_opencode_review(
            repo_path=repo_path,
            patch_content="",
            prompt=prompt,
            review_id=reviews[-1].review_request_id,
            model=model,
            timeout=timeout,
            binary_path=binary_path,
            at_reviewed_state=True,
        )
    except OpenCodeTimeoutError as e:
        raise click.ClickException(f"OpenCode timed out after {timeout}s") from e
    except OpenCodeError as e:
        raise click.ClickException(str(e)) from e
    finally:
        cleanup_deployed(deploy_result)
```

- [ ] **Step 3: Re-run grep**

Run: `grep -n "deploy_agent_skills" bb_review/cli/opencode.py`

Expected: TWO matches.

- [ ] **Step 4: Run unit tests + ruff**

Run: `uv run pytest tests/unit/ -v && uv run ruff check bb_review/`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bb_review/cli/opencode.py
git commit -m "feat: deploy native skill for opencode series reviews

Series path now mirrors single-review: deploy_agent_skills,
skill_name into the prompt, cleanup_deployed in finally."
```

---

## Task 6: Smoke test against a real review

**Files:** none modified. Manual verification only.

- [ ] **Step 1: Pick a recent net-drv-ts review id (or any RR for a repo with guides)**

Identify a recent RR id you have access to. If unsure: `uv run bb-review queue list | head` to see queued items.

- [ ] **Step 2: Dry-run with `--fake-review` to exercise the deploy without invoking OpenCode**

Run: `uv run bb-review opencode <RR_ID> --fake-review`

While it runs, in another shell:

```bash
ls -R <repo_path>/.opencode
```

(Use the path the CLI logs as the checkout location.)

Expected: see `.opencode/skill/net-drv-ts/SKILL.md`, `.opencode/skill/net-drv-ts/subsystem/`, and `.opencode/command/net-drv-ts-review.md`. After the command returns, the same `ls -R` should show those paths gone.

- [ ] **Step 3: Confirm placeholders are resolved**

After running, if you want to inspect the rendered file mid-flight, add a `time.sleep(30)` temporarily in `run_opencode_review` after the prompt is written, or run with `--keep-branch` and inspect. Alternatively, trust the unit tests — placeholder resolution is covered there.

- [ ] **Step 4: Run the full test suite + linters one more time**

Run: `uv run pytest tests/ -v && task lint`

Expected: all pass.

- [ ] **Step 5: Final review of git log**

Run: `git log --oneline -n 6`

Expected: six commits in order (test scaffold, refactor, prompt API, single-review wiring, series wiring, plus the spec commit from before).

---

## Done

All spec requirements covered:
- Native skill + command paths for OpenCode ✓ (Task 2)
- `subsystem/` reaches OpenCode ✓ (Task 2 via `_copy_supporting_files`)
- `{{GUIDE_DIR}}` / `{{REVIEW_GUIDE}}` resolved ✓ (Task 2, `_render_skill` opencode branch)
- Prompt uses `/{cmd}` invocation, parallel to Claude ✓ (Task 3)
- Single-review wiring ✓ (Task 4)
- Series wiring ✓ (Task 5)
- Tests for both Claude and OpenCode paths ✓ (Task 1)
- `_deploy_flat` deleted ✓ (Task 2)
- Cleanup unchanged in shape ✓ (already correct, exercised by tests)
