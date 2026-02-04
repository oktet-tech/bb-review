"""Tests for orchestration functions in bb_review/cli/_review_runner.py.

All external dependencies (repo manager, RB client, DB) are mocked.
"""

from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from bb_review.cli._review_runner import (
    _run_chain_review,
    _run_series_review,
    _run_single_review,
    run_review_command,
)
from bb_review.cli._session import ReviewSession
from bb_review.git import RepoManagerError
from bb_review.models import RepoConfig
from bb_review.rr.chain import ChainedReview, ChainError, ReviewChain
from bb_review.rr.rb_client import ReviewRequestInfo
from tests.mocks.rb_client import MockDiffInfo, MockRBClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo_config(tmp_path: Path) -> RepoConfig:
    return RepoConfig(
        name="test-repo",
        local_path=tmp_path / "repo",
        remote_url="git@example.com:org/test-repo.git",
        rb_repo_name="test-repo",
        default_branch="main",
    )


def _make_rb_client(rr_ids: list[int] | None = None) -> MockRBClient:
    rr_ids = rr_ids or [100]
    infos = {
        rr_id: ReviewRequestInfo(
            id=rr_id,
            summary=f"Review {rr_id}",
            status="pending",
            repository_name="test-repo",
            depends_on=[],
            base_commit_id="abc123",
            diff_revision=1,
        )
        for rr_id in rr_ids
    }
    diffs = {
        rr_id: MockDiffInfo(
            diff_revision=1,
            base_commit_id="abc123",
            raw_diff="diff --git a/f.c b/f.c\n--- a/f.c\n+++ b/f.c\n@@ -1 +1 @@\n-old\n+new\n",
        )
        for rr_id in rr_ids
    }
    return MockRBClient(review_request_infos=infos, diffs=diffs)


def _make_repo_manager(tmp_path: Path) -> MagicMock:
    """Build a mock RepoManager that yields tmp_path from context managers."""
    mgr = MagicMock()
    repo_config = _make_repo_config(tmp_path)
    mgr.get_repo_by_rb_name.return_value = repo_config

    @contextmanager
    def _checkout_ctx(repo_name, **kwargs):
        yield (tmp_path, True)

    @contextmanager
    def _chain_ctx(repo_name, base_commit, branch_name, keep_branch=False):
        yield tmp_path

    mgr.checkout_context.side_effect = _checkout_ctx
    mgr.chain_context.side_effect = _chain_ctx
    mgr.apply_and_commit.return_value = True
    mgr.apply_patch.return_value = True
    mgr.commit_staged.return_value = True
    return mgr


def _make_session(
    tmp_path: Path,
    rb_client: MockRBClient | None = None,
    repo_manager: MagicMock | None = None,
    fake_review: bool = True,
    series_reviewer_fn=None,
) -> ReviewSession:
    config = MagicMock()
    config.review_db.enabled = False
    config.reviewboard.url = "https://rb.example.com"

    return ReviewSession(
        config=config,
        rb_client=rb_client or _make_rb_client(),
        repo_manager=repo_manager or _make_repo_manager(tmp_path),
        repo_config=_make_repo_config(tmp_path),
        method_label="TestMethod",
        analysis_method="test",
        model="test-model",
        default_model="test-model",
        fake_review=fake_review,
        reviewer_fn=lambda *args: "mock analysis output",
        series_reviewer_fn=series_reviewer_fn,
    )


def _make_review(rr_id: int, needs_review: bool = True) -> ChainedReview:
    return ChainedReview(
        review_request_id=rr_id,
        summary=f"Review {rr_id}",
        status="pending" if needs_review else "submitted",
        diff_revision=1,
        base_commit_id="abc123",
        needs_review=needs_review,
    )


# ---------------------------------------------------------------------------
# run_review_command tests
# ---------------------------------------------------------------------------


class TestRunReviewCommand:
    def test_series_without_chain_raises(self, tmp_path):
        session = _make_session(tmp_path)
        with pytest.raises(click.UsageError, match="--series requires --chain"):
            run_review_command(
                session,
                100,
                timeout=60,
                dry_run=False,
                dump_response=None,
                output=None,
                auto_output=False,
                fallback=False,
                chain=False,
                chain_file=None,
                base_commit=None,
                keep_branch=False,
                review_from=None,
                series=True,
            )

    def test_series_without_series_fn_raises(self, tmp_path):
        session = _make_session(tmp_path, series_reviewer_fn=None)
        with pytest.raises(click.UsageError, match="series reviewer function"):
            run_review_command(
                session,
                100,
                timeout=60,
                dry_run=False,
                dump_response=None,
                output=None,
                auto_output=False,
                fallback=False,
                chain=True,
                chain_file=None,
                base_commit=None,
                keep_branch=False,
                review_from=None,
                series=True,
            )

    def test_series_with_review_from_raises(self, tmp_path):
        session = _make_session(
            tmp_path,
            series_reviewer_fn=lambda *a: "series output",
        )
        with pytest.raises(click.UsageError, match="incompatible"):
            run_review_command(
                session,
                100,
                timeout=60,
                dry_run=False,
                dump_response=None,
                output=None,
                auto_output=False,
                fallback=False,
                chain=True,
                chain_file=None,
                base_commit=None,
                keep_branch=False,
                review_from=99,
                series=True,
            )

    def test_review_from_not_in_chain_raises(self, tmp_path):
        session = _make_session(tmp_path)
        with pytest.raises(SystemExit):
            run_review_command(
                session,
                100,
                timeout=60,
                dry_run=False,
                dump_response=None,
                output=None,
                auto_output=False,
                fallback=False,
                chain=False,
                chain_file=None,
                base_commit=None,
                keep_branch=False,
                review_from=999,
            )

    def test_review_from_filters_correctly(self, tmp_path):
        """review_from skips earlier reviews in the chain."""
        rb = _make_rb_client([100, 101, 102])
        # Set up chain: 100 depends on nothing, 101 depends on 100, 102 on 101
        rb.review_request_infos[100] = ReviewRequestInfo(
            id=100,
            summary="Base",
            status="pending",
            repository_name="test-repo",
            depends_on=[],
            base_commit_id="abc123",
            diff_revision=1,
        )
        rb.review_request_infos[101] = ReviewRequestInfo(
            id=101,
            summary="Mid",
            status="pending",
            repository_name="test-repo",
            depends_on=[100],
            base_commit_id=None,
            diff_revision=1,
        )
        rb.review_request_infos[102] = ReviewRequestInfo(
            id=102,
            summary="Tip",
            status="pending",
            repository_name="test-repo",
            depends_on=[101],
            base_commit_id=None,
            diff_revision=1,
        )
        mgr = _make_repo_manager(tmp_path)
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)

        # review_from=101 should skip r/100
        run_review_command(
            session,
            102,
            timeout=60,
            dry_run=True,
            dump_response=None,
            output=None,
            auto_output=False,
            fallback=False,
            chain=True,
            chain_file=None,
            base_commit=None,
            keep_branch=False,
            review_from=101,
        )
        # If we got here without error, review_from was valid

    def test_no_pending_reviews_early_return(self, tmp_path, capsys):
        """Chain with all submitted reviews returns early."""
        rb = _make_rb_client([100])
        rb.review_request_infos[100] = ReviewRequestInfo(
            id=100,
            summary="Done",
            status="submitted",
            repository_name="test-repo",
            depends_on=[],
            base_commit_id="abc123",
            diff_revision=1,
        )
        mgr = _make_repo_manager(tmp_path)
        mgr.find_commit_by_summary = MagicMock(return_value="deadbeef")
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)

        run_review_command(
            session,
            100,
            timeout=60,
            dry_run=False,
            dump_response=None,
            output=None,
            auto_output=False,
            fallback=False,
            chain=True,
            chain_file=None,
            base_commit=None,
            keep_branch=False,
            review_from=None,
        )
        captured = capsys.readouterr()
        assert "No pending reviews" in captured.out

    def test_chain_error_exits(self, tmp_path):
        """ChainError causes sys.exit(1)."""
        rb = MagicMock()
        rb.get_review_request_info.side_effect = ChainError("boom")
        mgr = _make_repo_manager(tmp_path)
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)

        with pytest.raises(SystemExit) as exc_info:
            run_review_command(
                session,
                100,
                timeout=60,
                dry_run=False,
                dump_response=None,
                output=None,
                auto_output=False,
                fallback=False,
                chain=False,
                chain_file=None,
                base_commit=None,
                keep_branch=False,
                review_from=None,
            )
        assert exc_info.value.code == 1

    def test_dry_run_no_review(self, tmp_path, capsys):
        session = _make_session(tmp_path)
        run_review_command(
            session,
            100,
            timeout=60,
            dry_run=True,
            dump_response=None,
            output=None,
            auto_output=False,
            fallback=False,
            chain=False,
            chain_file=None,
            base_commit=None,
            keep_branch=False,
            review_from=None,
        )
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out


# ---------------------------------------------------------------------------
# _run_single_review tests
# ---------------------------------------------------------------------------


class TestRunSingleReview:
    def test_normal_flow(self, tmp_path):
        session = _make_session(tmp_path)
        review = _make_review(100)
        repo_config = _make_repo_config(tmp_path)

        _run_single_review(
            session,
            review,
            repo_config,
            timeout=60,
            dump_response=None,
            output=tmp_path / "out.json",
            auto_output=False,
            fallback=False,
        )
        out = tmp_path / "out.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["review_request_id"] == 100

    def test_fake_review_uses_mock(self, tmp_path, capsys):
        session = _make_session(tmp_path, fake_review=True)
        review = _make_review(100)
        repo_config = _make_repo_config(tmp_path)

        _run_single_review(
            session,
            review,
            repo_config,
            timeout=60,
            dump_response=None,
            output=tmp_path / "out.json",
            auto_output=False,
            fallback=False,
        )
        captured = capsys.readouterr()
        assert "FAKE REVIEW" in captured.out

    def test_dump_response_writes_file(self, tmp_path):
        session = _make_session(tmp_path)
        review = _make_review(100)
        repo_config = _make_repo_config(tmp_path)
        dump_path = tmp_path / "dump.txt"

        _run_single_review(
            session,
            review,
            repo_config,
            timeout=60,
            dump_response=dump_path,
            output=tmp_path / "out.json",
            auto_output=False,
            fallback=False,
        )
        assert dump_path.exists()
        assert len(dump_path.read_text()) > 0

    def test_repo_manager_error_exits(self, tmp_path):
        mgr = _make_repo_manager(tmp_path)

        @contextmanager
        def _fail_ctx(repo_name, **kwargs):
            raise RepoManagerError("checkout failed")

        mgr.checkout_context.side_effect = _fail_ctx
        session = _make_session(tmp_path, repo_manager=mgr)
        review = _make_review(100)
        repo_config = _make_repo_config(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            _run_single_review(
                session,
                review,
                repo_config,
                timeout=60,
                dump_response=None,
                output=None,
                auto_output=False,
                fallback=False,
            )
        assert exc_info.value.code == 1

    def test_auto_output_generates_filename(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        session = _make_session(tmp_path)
        review = _make_review(100)
        repo_config = _make_repo_config(tmp_path)

        _run_single_review(
            session,
            review,
            repo_config,
            timeout=60,
            dump_response=None,
            output=None,
            auto_output=True,
            fallback=False,
        )
        assert (tmp_path / "review_100.json").exists()


# ---------------------------------------------------------------------------
# _run_chain_review tests
# ---------------------------------------------------------------------------


class TestRunChainReview:
    def _make_chain(self, pending_ids, context_ids=None):
        chain = ReviewChain(repository="test-repo", base_commit="abc123")
        for rr_id in context_ids or []:
            chain.reviews.append(_make_review(rr_id, needs_review=False))
        for rr_id in pending_ids:
            chain.reviews.append(_make_review(rr_id, needs_review=True))
        return chain

    def test_context_patches_applied_before_pending(self, tmp_path, capsys):
        rb = _make_rb_client([50, 100, 101])
        mgr = _make_repo_manager(tmp_path)
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        chain = self._make_chain([100, 101], context_ids=[50])
        repo_config = _make_repo_config(tmp_path)

        _run_chain_review(
            session,
            101,
            chain,
            chain.pending_reviews,
            repo_config,
            timeout=60,
            dump_response=None,
            auto_output=False,
            fallback=False,
            keep_branch=False,
        )
        # apply_and_commit called for context patch r/50
        assert mgr.apply_and_commit.call_count >= 1
        first_call = mgr.apply_and_commit.call_args_list[0]
        assert "r/50" in first_call.args[2]

    def test_failed_context_patch_breaks(self, tmp_path, capsys):
        rb = _make_rb_client([50, 100])
        mgr = _make_repo_manager(tmp_path)
        mgr.apply_and_commit.return_value = False
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        chain = self._make_chain([100], context_ids=[50])
        repo_config = _make_repo_config(tmp_path)

        _run_chain_review(
            session,
            100,
            chain,
            chain.pending_reviews,
            repo_config,
            timeout=60,
            dump_response=None,
            auto_output=False,
            fallback=False,
            keep_branch=False,
        )
        captured = capsys.readouterr()
        assert "ERROR" in captured.err or "ERROR" in captured.out

    def test_fallback_on_patch_failure_continues(self, tmp_path, capsys):
        rb = _make_rb_client([100])
        mgr = _make_repo_manager(tmp_path)
        mgr.apply_patch.return_value = False
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        chain = self._make_chain([100])
        repo_config = _make_repo_config(tmp_path)

        _run_chain_review(
            session,
            100,
            chain,
            chain.pending_reviews,
            repo_config,
            timeout=60,
            dump_response=None,
            auto_output=False,
            fallback=True,
            keep_branch=False,
        )
        captured = capsys.readouterr()
        assert "fallback" in captured.err.lower() or "WARNING" in captured.err

    def test_no_fallback_on_patch_failure_breaks(self, tmp_path, capsys):
        rb = _make_rb_client([100])
        mgr = _make_repo_manager(tmp_path)
        mgr.apply_patch.return_value = False
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        chain = self._make_chain([100])
        repo_config = _make_repo_config(tmp_path)

        _run_chain_review(
            session,
            100,
            chain,
            chain.pending_reviews,
            repo_config,
            timeout=60,
            dump_response=None,
            auto_output=False,
            fallback=False,
            keep_branch=False,
        )
        captured = capsys.readouterr()
        assert "ERROR" in captured.err

    def test_auto_output_generates_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rb = _make_rb_client([100, 101])
        mgr = _make_repo_manager(tmp_path)
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        chain = self._make_chain([100, 101])
        repo_config = _make_repo_config(tmp_path)

        _run_chain_review(
            session,
            101,
            chain,
            chain.pending_reviews,
            repo_config,
            timeout=60,
            dump_response=None,
            auto_output=True,
            fallback=False,
            keep_branch=False,
        )
        assert (tmp_path / "review_100.json").exists()
        assert (tmp_path / "review_101.json").exists()

    def test_previous_patch_committed(self, tmp_path, monkeypatch):
        """When reviewing 2nd patch, the 1st patch should be committed."""
        monkeypatch.chdir(tmp_path)
        rb = _make_rb_client([100, 101])
        mgr = _make_repo_manager(tmp_path)
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        chain = self._make_chain([100, 101])
        repo_config = _make_repo_config(tmp_path)

        _run_chain_review(
            session,
            101,
            chain,
            chain.pending_reviews,
            repo_config,
            timeout=60,
            dump_response=None,
            auto_output=False,
            fallback=False,
            keep_branch=False,
        )
        # commit_staged should be called before 2nd review
        assert mgr.commit_staged.call_count == 1

    def test_db_saves_with_chain_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rb = _make_rb_client([100, 101])
        mgr = _make_repo_manager(tmp_path)
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        session.config.review_db.enabled = True

        chain = self._make_chain([100, 101])
        repo_config = _make_repo_config(tmp_path)

        with patch("bb_review.cli._review_runner.save_to_review_db") as mock_save:
            _run_chain_review(
                session,
                101,
                chain,
                chain.pending_reviews,
                repo_config,
                timeout=60,
                dump_response=None,
                auto_output=False,
                fallback=False,
                keep_branch=False,
            )
            assert mock_save.call_count == 2
            # Both calls should have chain_id set
            for call in mock_save.call_args_list:
                assert call.kwargs["chain_id"] is not None
                assert "bb_review_" in call.kwargs["chain_id"]


# ---------------------------------------------------------------------------
# _run_series_review tests
# ---------------------------------------------------------------------------


class TestRunSeriesReview:
    def _make_chain(self, rr_ids):
        chain = ReviewChain(repository="test-repo", base_commit="abc123")
        for rr_id in rr_ids:
            chain.reviews.append(_make_review(rr_id))
        return chain

    def test_all_patches_applied_as_commits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rb = _make_rb_client([100, 101, 102])
        mgr = _make_repo_manager(tmp_path)
        series_fn = MagicMock(return_value="series review output")
        session = _make_session(
            tmp_path,
            rb_client=rb,
            repo_manager=mgr,
            series_reviewer_fn=series_fn,
        )
        chain = self._make_chain([100, 101, 102])
        repo_config = _make_repo_config(tmp_path)

        _run_series_review(
            session,
            102,
            chain,
            repo_config,
            dump_response=None,
            output=tmp_path / "out.json",
            auto_output=False,
            keep_branch=False,
        )
        # apply_and_commit called once per patch
        assert mgr.apply_and_commit.call_count == 3

    def test_failed_patch_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rb = _make_rb_client([100, 101])
        mgr = _make_repo_manager(tmp_path)
        mgr.apply_and_commit.return_value = False
        series_fn = MagicMock()
        session = _make_session(
            tmp_path,
            rb_client=rb,
            repo_manager=mgr,
            series_reviewer_fn=series_fn,
        )
        chain = self._make_chain([100, 101])
        repo_config = _make_repo_config(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            _run_series_review(
                session,
                101,
                chain,
                repo_config,
                dump_response=None,
                output=None,
                auto_output=False,
                keep_branch=False,
            )
        assert exc_info.value.code == 1

    def test_series_reviewer_called_with_all_reviews(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rb = _make_rb_client([100, 101])
        mgr = _make_repo_manager(tmp_path)
        series_fn = MagicMock(return_value="series output")
        session = _make_session(
            tmp_path,
            rb_client=rb,
            repo_manager=mgr,
            fake_review=False,
            series_reviewer_fn=series_fn,
        )
        chain = self._make_chain([100, 101])
        repo_config = _make_repo_config(tmp_path)

        _run_series_review(
            session,
            101,
            chain,
            repo_config,
            dump_response=None,
            output=tmp_path / "out.json",
            auto_output=False,
            keep_branch=False,
        )
        series_fn.assert_called_once()
        call_args = series_fn.call_args
        reviews_arg = call_args[0][0]
        assert len(reviews_arg) == 2
        assert reviews_arg[0].review_request_id == 100

    def test_output_saved_to_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rb = _make_rb_client([100])
        mgr = _make_repo_manager(tmp_path)
        session = _make_session(tmp_path, rb_client=rb, repo_manager=mgr)
        chain = self._make_chain([100])
        repo_config = _make_repo_config(tmp_path)
        out_path = tmp_path / "series_out.json"

        _run_series_review(
            session,
            100,
            chain,
            repo_config,
            dump_response=None,
            output=out_path,
            auto_output=False,
            keep_branch=False,
        )
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert data["review_request_id"] == 100
