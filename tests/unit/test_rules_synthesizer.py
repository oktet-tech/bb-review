"""Tests for the rules-mining synthesis helpers."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bb_review.db.mining_db import MinedComment, MiningDatabase
from bb_review.rules.synthesizer import (
    RulesDraftError,
    build_rules_prompt,
    draft_rules,
    format_comments_artifact,
)
from bb_review.triage.models import RBComment


def _comment(**kw) -> MinedComment:
    defaults = dict(
        rr_id=1,
        rr_status="submitted",
        review_id=10,
        comment_id=20,
        reviewer="alice",
        text="check the return value",
        file_path="src/a.c",
        line_number=42,
        is_body_comment=False,
        issue_opened=True,
        issue_status="resolved",
        reply_to_id=None,
    )
    defaults.update(kw)
    return MinedComment(**defaults)


def test_format_comments_artifact_groups_by_file():
    comments = [
        _comment(file_path="src/a.c", text="comment one"),
        _comment(file_path="src/b.c", text="comment two"),
    ]
    artifact = format_comments_artifact(comments)
    assert "## src/a.c" in artifact
    assert "## src/b.c" in artifact
    assert "comment one" in artifact
    assert "comment two" in artifact
    assert "Total comments: 2" in artifact


def test_format_comments_artifact_tags_status_and_rr():
    artifact = format_comments_artifact([_comment(rr_id=77, issue_status="dropped")])
    assert "RR #77" in artifact
    assert "dropped" in artifact
    assert "reviewer: alice" in artifact


def test_format_comments_artifact_handles_body_comments():
    artifact = format_comments_artifact(
        [_comment(file_path=None, is_body_comment=True, text="overall looks fine")]
    )
    assert "(general / body comments)" in artifact
    assert "overall looks fine" in artifact


def test_build_rules_prompt_includes_repo_and_sections():
    prompt = build_rules_prompt("myrepo", "ARTIFACT TEXT", existing_patterns=None)
    assert "myrepo" in prompt
    assert "Recurring Mistakes" in prompt
    assert "False-Positive Candidates" in prompt
    assert ".bb_review_mined_comments.md" in prompt


def test_build_rules_prompt_includes_existing_patterns():
    prompt = build_rules_prompt("myrepo", "ARTIFACT", existing_patterns="EXISTING RULES BLOCK")
    assert "EXISTING RULES BLOCK" in prompt
    assert "only output rules that are NEW" in prompt


class FakeRepoManager:
    """Minimal RepoManager stand-in for draft_rules tests."""

    def __init__(self, repo_path: Path, default_branch: str = "main"):
        self._repo_path = repo_path
        self._default_branch = default_branch
        self.checked_out: list[str] = []

    def ensure_clone(self, name: str) -> None:
        return None

    def get_repo(self, name: str):
        return SimpleNamespace(default_branch=self._default_branch)

    def checkout(self, name: str, ref: str) -> None:
        self.checked_out.append(ref)

    def get_local_path(self, name: str) -> Path:
        return self._repo_path


def _seed_db(tmp_path: Path) -> MiningDatabase:
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=1,
        repository="myrepo",
        rr_status="submitted",
        rr_summary="s",
        submitter="bob",
        branch="main",
        rb_last_updated="d",
        comments=[RBComment(review_id=2, comment_id=3, reviewer="a", text="bug here")],
    )
    return db


def test_draft_rules_raises_when_no_comments(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    repo_path = tmp_path / "checkout"
    repo_path.mkdir()

    with pytest.raises(RulesDraftError, match="No cached comments"):
        draft_rules(
            repo_name="myrepo",
            mining_db=db,
            repo_manager=FakeRepoManager(repo_path),
            guides_dir=tmp_path / "guides",
            run_agent_fn=lambda **kw: "unused",
        )


def test_draft_rules_writes_draft_file(tmp_path: Path):
    db = _seed_db(tmp_path)
    repo_path = tmp_path / "checkout"
    repo_path.mkdir()
    guides_dir = tmp_path / "guides"

    captured = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "# Draft Review Rules: myrepo\n\nrule one"

    out_path = draft_rules(
        repo_name="myrepo",
        mining_db=db,
        repo_manager=FakeRepoManager(repo_path),
        guides_dir=guides_dir,
        run_agent_fn=fake_run_agent,
    )

    assert out_path == guides_dir / "myrepo" / "draft-rules.md"
    assert out_path.read_text() == "# Draft Review Rules: myrepo\n\nrule one"
    # The mined-comments artifact is cleaned up after the agent runs.
    assert not (repo_path / ".bb_review_mined_comments.md").exists()
    # The agent prompt mentions the repo and the comment text.
    assert "myrepo" in captured["prompt"]


def test_draft_rules_includes_existing_patterns(tmp_path: Path):
    db = _seed_db(tmp_path)
    repo_path = tmp_path / "checkout"
    repo_path.mkdir()
    guides_dir = tmp_path / "guides"
    tp_dir = guides_dir / "myrepo"
    tp_dir.mkdir(parents=True)
    (tp_dir / "technical-patterns.md").write_text("ALREADY DOCUMENTED RULE")

    captured = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "# Draft Review Rules: myrepo\n\nnew rule"

    draft_rules(
        repo_name="myrepo",
        mining_db=db,
        repo_manager=FakeRepoManager(repo_path),
        guides_dir=guides_dir,
        run_agent_fn=fake_run_agent,
    )
    assert "ALREADY DOCUMENTED RULE" in captured["prompt"]
