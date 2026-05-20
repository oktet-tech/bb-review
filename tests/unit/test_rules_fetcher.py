"""Tests for the rules-mining fetch orchestration."""

from pathlib import Path

from bb_review.db.mining_db import MiningDatabase
from bb_review.rules.fetcher import fetch_repo_rules_data
from bb_review.triage.models import RBComment
from tests.mocks import MockDiffInfo, MockRBClient


class FakeCommentFetcher:
    """Stand-in for RBCommentFetcher with canned per-RR comments."""

    def __init__(self, comments_by_rr: dict[int, list[RBComment]], fail_on: int | None = None):
        self.comments_by_rr = comments_by_rr
        self.fail_on = fail_on

    def fetch_all_comments(self, rr_id: int) -> list[RBComment]:
        if self.fail_on is not None and rr_id == self.fail_on:
            raise RuntimeError("simulated fetch failure")
        return self.comments_by_rr.get(rr_id, [])


def _rr(rr_id: int, status: str = "submitted") -> dict:
    return {
        "id": rr_id,
        "summary": f"RR {rr_id}",
        "status": status,
        "last_updated": "2026-05-10T00:00:00",
        "branch": "main",
        "links": {"submitter": {"title": "alice"}},
    }


def test_fetch_records_new_and_skips_cached(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=1,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="old",
        submitter="x",
        branch="main",
        rb_last_updated="d",
        comments=[],
    )
    rb = MockRBClient(repo_review_requests=[_rr(1), _rr(2, "discarded")])
    fetcher = FakeCommentFetcher({2: [RBComment(review_id=7, comment_id=8, reviewer="bob", text="nit")]})

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="testrepo",
        rb_repo_name="test-repo",
        bot_username="bot",
        count=30,
        comment_fetcher=fetcher,
    )

    assert counts["total"] == 2
    assert counts["fetched"] == 1
    assert counts["skipped"] == 1
    assert counts["comments"] == 1
    assert db.has_review_request(2) is True


def test_fetch_refresh_re_fetches_cached(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    db.record_review_request(
        rr_id=1,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="old",
        submitter="x",
        branch="main",
        rb_last_updated="d",
        comments=[],
    )
    rb = MockRBClient(repo_review_requests=[_rr(1)])
    fetcher = FakeCommentFetcher(
        {1: [RBComment(review_id=3, comment_id=4, reviewer="bob", text="re-fetched")]}
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="testrepo",
        rb_repo_name="test-repo",
        bot_username="bot",
        count=30,
        refresh=True,
        comment_fetcher=fetcher,
    )

    assert counts["fetched"] == 1
    assert counts["skipped"] == 0
    assert db.get_repo_stats("testrepo").comment_count == 1


def test_fetch_continues_after_per_rr_error(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    rb = MockRBClient(repo_review_requests=[_rr(1), _rr(2)])
    fetcher = FakeCommentFetcher(
        {2: [RBComment(review_id=9, comment_id=10, reviewer="c", text="ok")]},
        fail_on=1,
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="testrepo",
        rb_repo_name="test-repo",
        bot_username="bot",
        count=30,
        comment_fetcher=fetcher,
    )

    assert counts["total"] == 2
    assert counts["fetched"] == 1
    assert db.has_review_request(1) is False
    assert db.has_review_request(2) is True


class _RecordingCommentFetcher:
    """Comment fetcher returning canned comments and counting calls."""

    def __init__(self, comments_by_rr: dict[int, list[RBComment]]):
        self.comments_by_rr = comments_by_rr
        self.calls: list[int] = []

    def fetch_all_comments(self, rr_id: int) -> list[RBComment]:
        self.calls.append(rr_id)
        return self.comments_by_rr.get(rr_id, [])


def _diff_for_line(file_path: str, line: int) -> str:
    """Build a tiny unified diff whose new-file hunk covers `line`."""
    start = max(1, line - 1)
    return (
        f"diff --git a/{file_path} b/{file_path}\n"
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        f"@@ -{start},2 +{start},3 @@\n"
        " context_before\n"
        "+added_line\n"
        " context_after\n"
    )


def test_fetch_with_diff_hunks_populates_hunk_for_diff_comments(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    rr = {
        "id": 1,
        "summary": "x",
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "branch": "main",
        "links": {"submitter": {"title": "alice"}},
    }
    raw_diff = _diff_for_line("src/a.c", 2)
    rb = MockRBClient(
        repo_review_requests=[rr],
        diffs_by_rev={(1, 3): MockDiffInfo(diff_revision=3, raw_diff=raw_diff)},
    )
    fetcher = _RecordingCommentFetcher(
        {
            1: [
                RBComment(
                    review_id=10,
                    comment_id=20,
                    reviewer="bob",
                    text="check",
                    file_path="src/a.c",
                    line_number=2,
                    diff_revision=3,
                ),
                RBComment(
                    review_id=10,
                    comment_id=21,
                    reviewer="bob",
                    text="general",
                    is_body_comment=True,
                ),
            ]
        }
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=True,
        comment_fetcher=fetcher,
    )
    assert counts["fetched"] == 1
    assert counts["hunks_backfilled"] == 0

    saved = db.get_comments_for_repo("r")
    by_id = {c.comment_id: c for c in saved}
    assert "@@ -1,2 +1,3 @@" in (by_id[20].diff_hunk or "")
    assert by_id[21].diff_hunk is None  # body comment, untouched


def test_fetch_memoizes_diff_per_rr_revision(tmp_path: Path, monkeypatch):
    """Multiple comments on the same (rr_id, rev) must trigger get_diff once."""
    db = MiningDatabase(tmp_path / "m.db")
    rr = {
        "id": 1,
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "links": {"submitter": {"title": "alice"}},
    }
    raw_diff = _diff_for_line("src/a.c", 2)
    rb = MockRBClient(
        repo_review_requests=[rr],
        diffs_by_rev={(1, 3): MockDiffInfo(diff_revision=3, raw_diff=raw_diff)},
    )

    call_count = {"n": 0}
    original_get_diff = rb.get_diff

    def counting_get_diff(*args, **kw):
        call_count["n"] += 1
        return original_get_diff(*args, **kw)

    monkeypatch.setattr(rb, "get_diff", counting_get_diff)

    fetcher = _RecordingCommentFetcher(
        {
            1: [
                RBComment(
                    review_id=10,
                    comment_id=20 + i,
                    reviewer="bob",
                    text="t",
                    file_path="src/a.c",
                    line_number=2,
                    diff_revision=3,
                )
                for i in range(5)
            ]
        }
    )

    fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=True,
        comment_fetcher=fetcher,
    )
    assert call_count["n"] == 1  # five comments, one diff fetch


def test_fetch_with_diff_hunks_continues_when_get_diff_fails(tmp_path: Path):
    db = MiningDatabase(tmp_path / "m.db")
    rr = {
        "id": 1,
        "status": "submitted",
        "last_updated": "2026-05-10T00:00:00",
        "links": {"submitter": {"title": "alice"}},
    }
    rb = MockRBClient(repo_review_requests=[rr])

    def failing_get_diff(*args, **kw):
        raise RuntimeError("simulated diff failure")

    rb.get_diff = failing_get_diff
    fetcher = _RecordingCommentFetcher(
        {
            1: [
                RBComment(
                    review_id=10,
                    comment_id=20,
                    reviewer="bob",
                    text="t",
                    file_path="src/a.c",
                    line_number=2,
                    diff_revision=3,
                )
            ]
        }
    )

    counts = fetch_repo_rules_data(
        rb_client=rb,
        mining_db=db,
        repo_name="r",
        rb_repo_name="rb",
        bot_username="bot",
        count=10,
        with_diff_hunks=True,
        comment_fetcher=fetcher,
    )
    # The RR was still recorded; only the hunk is missing.
    assert counts["fetched"] == 1
    saved = db.get_comments_for_repo("r")
    assert saved[0].diff_hunk is None
