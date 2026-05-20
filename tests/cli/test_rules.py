"""Tests for the rules CLI commands."""

from pathlib import Path

from click.testing import CliRunner
import pytest

from bb_review.cli import main
from bb_review.db.mining_db import MiningDatabase
from bb_review.triage.models import RBComment


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    content = f"""
reviewboard:
  url: "https://rb.example.com"
  api_token: "test-token"
  bot_username: "ai-reviewer"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "test-key"
database:
  path: "{tmp_path / "state.db"}"
repositories:
  - name: testrepo
    rb_repo_name: test-repo
    local_path: "{tmp_path / "testrepo"}"
    remote_url: "https://git.example.com/testrepo.git"
"""
    path = tmp_path / "config.yaml"
    path.write_text(content)
    return path


def _mining_db(tmp_path: Path) -> MiningDatabase:
    return MiningDatabase(tmp_path / "rules_mining.db")


def test_rules_show_empty(runner: CliRunner, config_path: Path):
    result = runner.invoke(main, ["--config", str(config_path), "rules", "show", "testrepo"])
    assert result.exit_code == 0
    assert "Review requests: 0" in result.output


def test_rules_show_with_data(runner: CliRunner, config_path: Path, tmp_path: Path):
    db = _mining_db(tmp_path)
    db.record_review_request(
        rr_id=1,
        repository="testrepo",
        rr_status="submitted",
        rr_summary="s",
        submitter="bob",
        branch="main",
        rb_last_updated="d",
        comments=[RBComment(review_id=2, comment_id=3, reviewer="a", text="t")],
    )
    result = runner.invoke(main, ["--config", str(config_path), "rules", "show", "testrepo"])
    assert result.exit_code == 0
    assert "Review requests: 1" in result.output
    assert "Comments:        1" in result.output


def test_rules_fetch_reports_counts(runner: CliRunner, config_path: Path, monkeypatch):
    class StubRBClient:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            return None

    monkeypatch.setattr("bb_review.rr.rb_client.ReviewBoardClient", StubRBClient)
    monkeypatch.setattr(
        "bb_review.cli.rules.fetch_repo_rules_data",
        lambda **kw: {"total": 3, "fetched": 2, "skipped": 1, "comments": 7},
    )

    result = runner.invoke(main, ["--config", str(config_path), "rules", "fetch", "testrepo"])
    assert result.exit_code == 0
    assert "3 RRs found" in result.output
    assert "2 fetched" in result.output
    assert "7 comments cached" in result.output


def test_rules_fetch_unknown_repo(runner: CliRunner, config_path: Path):
    result = runner.invoke(main, ["--config", str(config_path), "rules", "fetch", "nope"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_rules_draft_writes_file(runner: CliRunner, config_path: Path, monkeypatch, tmp_path: Path):
    out_file = tmp_path / "draft-rules.md"
    monkeypatch.setattr(
        "bb_review.cli.rules.draft_rules",
        lambda **kw: out_file,
    )
    result = runner.invoke(main, ["--config", str(config_path), "rules", "draft", "testrepo"])
    assert result.exit_code == 0
    assert f"Wrote {out_file}" in result.output


def test_rules_draft_forwards_model(runner: CliRunner, config_path: Path, monkeypatch, tmp_path: Path):
    out_file = tmp_path / "draft-rules.md"
    captured = {}

    def _capture(**kw):
        captured.update(kw)
        return out_file

    monkeypatch.setattr("bb_review.cli.rules.draft_rules", _capture)
    result = runner.invoke(
        main,
        ["--config", str(config_path), "rules", "draft", "testrepo", "--model", "opus"],
    )
    assert result.exit_code == 0
    assert captured["model"] == "opus"


def test_rules_draft_handles_missing_cache(runner: CliRunner, config_path: Path, monkeypatch):
    from bb_review.rules.synthesizer import RulesDraftError

    def _raise(**kw):
        raise RulesDraftError("No cached comments for 'testrepo'.")

    monkeypatch.setattr("bb_review.cli.rules.draft_rules", _raise)
    result = runner.invoke(main, ["--config", str(config_path), "rules", "draft", "testrepo"])
    assert result.exit_code == 1
    assert "No cached comments" in result.output


def test_rules_fetch_forwards_with_diff_hunks(runner: CliRunner, config_path: Path, monkeypatch):
    class StubRBClient:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            return None

    monkeypatch.setattr("bb_review.rr.rb_client.ReviewBoardClient", StubRBClient)

    captured = {}

    def fake_fetch(**kw):
        captured.update(kw)
        return {
            "total": 0,
            "fetched": 0,
            "skipped": 0,
            "comments": 0,
            "hunks_backfilled": 0,
        }

    monkeypatch.setattr("bb_review.cli.rules.fetch_repo_rules_data", fake_fetch)

    result = runner.invoke(
        main,
        ["--config", str(config_path), "rules", "fetch", "testrepo", "--with-diff-hunks"],
    )
    assert result.exit_code == 0
    assert captured["with_diff_hunks"] is True


def test_rules_fetch_reports_hunks_backfilled(runner: CliRunner, config_path: Path, monkeypatch):
    class StubRBClient:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            return None

    monkeypatch.setattr("bb_review.rr.rb_client.ReviewBoardClient", StubRBClient)
    monkeypatch.setattr(
        "bb_review.cli.rules.fetch_repo_rules_data",
        lambda **kw: {
            "total": 3,
            "fetched": 1,
            "skipped": 1,
            "comments": 4,
            "hunks_backfilled": 1,
        },
    )
    result = runner.invoke(
        main,
        ["--config", str(config_path), "rules", "fetch", "testrepo", "--with-diff-hunks"],
    )
    assert result.exit_code == 0
    assert "1 hunks backfilled" in result.output
