"""Tests for the rules-mining synthesis helpers."""

from bb_review.db.mining_db import MinedComment
from bb_review.rules.synthesizer import build_rules_prompt, format_comments_artifact


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
