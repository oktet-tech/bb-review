"""Tests for the rules-mining fetch orchestration."""

from pathlib import Path

from bb_review.db.mining_db import MiningDatabase
from bb_review.rules.fetcher import fetch_repo_rules_data
from bb_review.triage.models import RBComment
from tests.mocks import MockRBClient


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
