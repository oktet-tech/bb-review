"""Tests for plan_writer YAML round-trip from bb_review/triage/plan_writer.py."""

from datetime import datetime

from bb_review.triage.models import (
    CommentClassification,
    Difficulty,
    FixPlan,
    FixPlanItem,
    TriageAction,
)
from bb_review.triage.plan_writer import read_fix_plan, write_fix_plan


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanWriterRoundTrip:
    def test_write_and_read_basic_plan(self, tmp_path):
        plan = FixPlan(
            review_request_id=42763,
            repository="test-repo",
            items=[
                FixPlanItem(
                    comment_id=100,
                    action=TriageAction.FIX,
                    file_path="src/main.c",
                    line_number=42,
                    classification=CommentClassification.VALID,
                    difficulty=Difficulty.SIMPLE,
                    reviewer="alice",
                    original_text="Missing null check",
                    fix_hint="Add NULL check before line 42",
                ),
                FixPlanItem(
                    comment_id=101,
                    action=TriageAction.REPLY,
                    classification=CommentClassification.CONFUSED,
                    reviewer="bob",
                    original_text="Why not factory?",
                    reply_text="Factory adds complexity here",
                ),
            ],
        )

        path = tmp_path / "plan.yaml"
        write_fix_plan(plan, path)

        loaded = read_fix_plan(path)

        assert loaded.review_request_id == 42763
        assert loaded.repository == "test-repo"
        assert len(loaded.items) == 2

        fix = loaded.items[0]
        assert fix.action == TriageAction.FIX
        assert fix.file_path == "src/main.c"
        assert fix.line_number == 42
        assert fix.classification == CommentClassification.VALID
        assert fix.difficulty == Difficulty.SIMPLE
        assert fix.fix_hint == "Add NULL check before line 42"

        reply = loaded.items[1]
        assert reply.action == TriageAction.REPLY
        assert reply.reply_text == "Factory adds complexity here"
        assert reply.file_path is None

    def test_empty_plan(self, tmp_path):
        plan = FixPlan(review_request_id=100, repository="repo")
        path = tmp_path / "empty.yaml"
        write_fix_plan(plan, path)

        loaded = read_fix_plan(path)
        assert loaded.items == []
        assert loaded.review_request_id == 100

    def test_all_actions_roundtrip(self, tmp_path):
        items = []
        for i, action in enumerate(TriageAction):
            items.append(
                FixPlanItem(
                    comment_id=i,
                    action=action,
                    reviewer="user",
                    original_text=f"Comment {i}",
                )
            )
        plan = FixPlan(review_request_id=1, repository="r", items=items)

        path = tmp_path / "actions.yaml"
        write_fix_plan(plan, path)
        loaded = read_fix_plan(path)

        loaded_actions = [item.action for item in loaded.items]
        assert loaded_actions == list(TriageAction)

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "plan.yaml"
        plan = FixPlan(review_request_id=1, repository="r")
        write_fix_plan(plan, path)
        assert path.exists()

    def test_preserves_created_at(self, tmp_path):
        ts = datetime(2026, 2, 6, 14, 30, 0)
        plan = FixPlan(review_request_id=1, repository="r", created_at=ts)
        path = tmp_path / "ts.yaml"
        write_fix_plan(plan, path)

        loaded = read_fix_plan(path)
        assert loaded.created_at.year == 2026
        assert loaded.created_at.month == 2
        assert loaded.created_at.day == 6


class TestFixPlanProperties:
    def test_counts(self):
        plan = FixPlan(
            review_request_id=1,
            repository="r",
            items=[
                FixPlanItem(comment_id=1, action=TriageAction.FIX),
                FixPlanItem(comment_id=2, action=TriageAction.FIX),
                FixPlanItem(comment_id=3, action=TriageAction.REPLY),
                FixPlanItem(comment_id=4, action=TriageAction.SKIP),
                FixPlanItem(comment_id=5, action=TriageAction.DISAGREE),
            ],
        )
        assert plan.fix_count == 2
        assert plan.reply_count == 2  # REPLY + DISAGREE
        assert plan.skip_count == 1
