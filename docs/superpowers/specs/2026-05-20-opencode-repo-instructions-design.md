# OpenCode Repo Instructions — Design

Date: 2026-05-20
Status: Draft

## Problem

OpenCode reviews currently get the repo-specific guides through a flat-copy
deploy into `.opencode/`. Two concrete defects:

1. `_deploy_flat()` in `bb_review/guidelines_deploy.py:215-245` does **not**
   copy the `guides/{repo}/subsystem/` directory — subsystem guides never
   reach OpenCode.
2. The SKILL.md body uses `{{GUIDE_DIR}}` and `{{REVIEW_GUIDE}}` placeholders.
   The flat path leaves them unresolved.
3. The prompt then asks OpenCode to `read each file` by listing paths, instead
   of letting OpenCode auto-discover a skill the way Claude does.

Claude Code already has the right shape: `.claude/skills/{repo}/SKILL.md` +
supporting files + `.claude/commands/{cmd}.md`, with placeholder substitution
via `_render_skill`. The prompt then says `invoke /{cmd}`.

OpenCode (>= v1.0.190) natively supports the same model under different
paths: `.opencode/skill/{repo}/SKILL.md` and `.opencode/command/{cmd}.md`
(both singular).

## Goal

Deliver `guides/{repo}/` content to OpenCode using OpenCode's native skill +
command discovery, mirroring the Claude path. Single mental model for both
agents.

## Non-goals

- Generating `AGENTS.md` in the repo checkout.
- Mutating any existing `opencode.json` in the repo.
- Version-detecting OpenCode to gate behavior. Old OpenCode silently ignores
  the deployed skill; that is no worse than today.

## Design

### Deploy layout (after change)

```
guides/{repo}/                       # source (unchanged)
  skills/{repo}.md                   # SKILL.md body with placeholders
  slash-commands/{cmd}.md            # review protocol
  technical-patterns.md
  false-positive-guide.md
  subsystem/{*.md}

repo checkout, deployed per run:
  Claude:    .claude/skills/{repo}/        + .claude/commands/{cmd}.md
  OpenCode:  .opencode/skill/{repo}/       + .opencode/command/{cmd}.md   (new)
  Codex:     .agents/skills/{repo}/        (cmd inside skill dir)         (unchanged)
```

### Code changes

**`bb_review/guidelines_deploy.py`**

- Factor `_deploy_claude` and the new OpenCode path into one helper:

  ```python
  _SKILL_AWARE_PATHS = {
      "claude":   (".claude/skills",   ".claude/commands"),
      "opencode": (".opencode/skill",  ".opencode/command"),  # singular
  }

  def _deploy_skill_with_commands(repo_path, guides_dir, repo_name, agent):
      skill_root, cmd_root = _SKILL_AWARE_PATHS[agent]
      ...
  ```

  The helper handles skill dir creation, SKILL.md rendering via
  `_render_skill`, supporting-file copy, and command file copy.

- Extend `_render_skill` with an `opencode` branch:

  ```python
  elif agent == "opencode":
      guide_dir   = f".opencode/skill/{repo_name}"
      review_guide = (
          f"invoke the `/{review_cmd}` command" if review_cmd
          else "follow the review protocol"
      )
  ```

- Update `deploy_agent_skills` dispatcher:

  ```python
  if agent_type in _SKILL_AWARE_PATHS:
      result = _deploy_skill_with_commands(repo_path, guides_dir, repo_name, agent_type)
  elif agent_type == "codex":
      result = _deploy_codex(...)
  else:
      logger.warning(f"Unknown agent type for skill deployment: {agent_type}")
      return DeployResult()
  ```

- Delete `_deploy_flat` and `_AGENT_DIRS`. They are unused after this change.

**`bb_review/reviewers/opencode.py`**

- Replace `skill_files: list[str] | None` with `skill_name: str | None` in
  both `build_review_prompt` and `build_series_review_prompt`.
- When `skill_name` is set, render the same wording the Claude path uses:

  ```
  IMPORTANT: Before starting your review, invoke the /{skill_name} skill.
  It contains project-specific conventions, technical patterns, and
  subsystem-specific guidance. Follow it strictly, including any review
  command or subsystem guides it directs you to.
  ```

  (Wording stays in sync with `reviewers/claude_code.py:149-154`.)

**`bb_review/cli/opencode.py`**

- `run_opencode_for_review` and the series path pass
  `skill_name=deploy_result.skill_name` instead of the current
  `skill_files=[str(p.relative_to(repo_path)) for p in deploy_result.deployed_files]`.

### Behavior

1. `deploy_agent_skills(repo_path, repo_name, "opencode")` writes
   `.opencode/skill/{repo}/SKILL.md` (placeholders resolved), supporting files,
   subsystem dir, and `.opencode/command/{cmd}.md`. Returns
   `DeployResult(skill_name=repo_name, deployed_dirs=[skill_dir],
   deployed_files=[cmd_file])`.
2. CLI builds a prompt that says `invoke /{cmd}`.
3. OpenCode auto-discovers the skill and command; the model loads SKILL.md and
   then the command file when instructed.
4. After the run, `cleanup_deployed(deploy_result)` removes both the skill dir
   and the command file. Existing `finally` block is unchanged.

## Error handling

- Missing `guides/{repo}/skills/{repo}.md` → `DeployResult()` empty → prompt
  uses the existing inline `guidelines_context` fallback (no regression).
- OpenCode < 1.0.190 → skill is ignored. Review proceeds with whatever
  context the prompt body provides. Acceptable; documented.
- Deploy/cleanup failures already swallow exceptions silently (existing
  behavior). Keep.

## Testing

- **Unit** `tests/unit/test_guidelines_deploy.py`:
  - Parametrize over `agent in ("claude", "opencode")`.
  - Assert deployed paths under expected roots, including `subsystem/` dir.
  - Assert `{{GUIDE_DIR}}` and `{{REVIEW_GUIDE}}` resolved correctly per agent.
  - Assert `cleanup_deployed` removes everything created.
- **Unit** prompt builder test: when `skill_name` set, prompt contains
  `invoke /{skill_name}` and **omits** the file-by-file listing.
- **Manual**: `uv run bb-review opencode <net-drv-ts review id> --dry-run`;
  inspect `.opencode/` while running; confirm gone after.

## Migration / compatibility

- No config schema changes.
- Repos that had `.opencode/` files from prior runs: cleanup is best-effort;
  any orphaned files from earlier flat deploys (e.g. left from a crashed
  process) will not be removed by this change. Acceptable — they were never
  guaranteed clean.

## Open questions

None.
