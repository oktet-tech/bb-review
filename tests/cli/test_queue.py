"""Tests for the queue CLI commands."""

from pathlib import Path

from click.testing import CliRunner
import pytest

from bb_review.cli import main
from bb_review.db.queue_db import QueueDatabase
from bb_review.db.queue_models import QueueStatus


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def config_with_db(tmp_path: Path) -> Path:
    config_content = f"""
reviewboard:
  url: "https://rb.example.com"
  api_token: "test-token"
  bot_username: "ai-reviewer"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "test-key"
review_db:
  enabled: true
  path: "{tmp_path / "reviews.db"}"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def queue_db(config_with_db: Path, tmp_path: Path) -> QueueDatabase:
    """Create QueueDatabase pointing to same path as config."""
    db_path = tmp_path / "reviews.db"
    return QueueDatabase(db_path)


@pytest.fixture
def populated_queue(queue_db: QueueDatabase, config_with_db: Path) -> Path:
    """Config with a populated queue."""
    queue_db.upsert(
        review_request_id=42738,
        diff_revision=1,
        repository="test-repo",
        submitter="alice",
        summary="Fix the widget",
    )
    queue_db.upsert(
        review_request_id=42739,
        diff_revision=2,
        repository="test-repo",
        submitter="bob",
        summary="Add feature X",
    )
    queue_db.update_status(42739, QueueStatus.NEXT)
    return config_with_db


class TestQueueList:
    def test_list_empty(self, runner: CliRunner, config_with_db: Path):
        result = runner.invoke(main, ["--config", str(config_with_db), "queue", "list"])
        assert result.exit_code == 0
        assert "No queue items found" in result.output

    def test_list_with_items(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(main, ["--config", str(populated_queue), "queue", "list"])
        assert result.exit_code == 0
        assert "42738" in result.output
        assert "42739" in result.output
        assert "test-repo" in result.output

    def test_list_filter_status(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(main, ["--config", str(populated_queue), "queue", "list", "--status", "next"])
        assert result.exit_code == 0
        assert "42739" in result.output
        assert "42738" not in result.output

    def test_list_filter_repo(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(
            main, ["--config", str(populated_queue), "queue", "list", "--repo", "test-repo"]
        )
        assert result.exit_code == 0
        assert "42738" in result.output


class TestQueueSet:
    def test_set_status(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(
            main, ["--config", str(populated_queue), "queue", "set", "42738", "--status", "next"]
        )
        assert result.exit_code == 0
        assert "todo -> next" in result.output

    def test_set_multiple(self, runner: CliRunner, populated_queue: Path, queue_db: QueueDatabase):
        # First set 42738 to next
        queue_db.update_status(42738, QueueStatus.NEXT)

        result = runner.invoke(
            main,
            [
                "--config",
                str(populated_queue),
                "queue",
                "set",
                "42738",
                "42739",
                "--status",
                "ignore",
            ],
        )
        assert result.exit_code == 0
        assert "42738: next -> ignore" in result.output
        assert "42739: next -> ignore" in result.output

    def test_set_invalid_transition(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(
            main, ["--config", str(populated_queue), "queue", "set", "42738", "--status", "done"]
        )
        # Should print error but not crash
        assert "Cannot transition" in result.output


class TestQueueStats:
    def test_stats_empty(self, runner: CliRunner, config_with_db: Path):
        result = runner.invoke(main, ["--config", str(config_with_db), "queue", "stats"])
        assert result.exit_code == 0
        assert "Total: 0" in result.output

    def test_stats_with_data(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(main, ["--config", str(populated_queue), "queue", "stats"])
        assert result.exit_code == 0
        assert "Total: 2" in result.output
        assert "todo: 1" in result.output
        assert "next: 1" in result.output


class TestQueueShow:
    def test_show_item(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(main, ["--config", str(populated_queue), "queue", "show", "42738"])
        assert result.exit_code == 0
        assert "r/42738" in result.output
        assert "todo" in result.output
        assert "test-repo" in result.output
        assert "alice" in result.output
        assert "Fix the widget" in result.output

    def test_show_not_found(self, runner: CliRunner, config_with_db: Path):
        result = runner.invoke(main, ["--config", str(config_with_db), "queue", "show", "99999"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestInteractiveQueue:
    def test_interactive_queue_launches_unified(self, runner: CliRunner, config_with_db: Path):
        """Unified TUI launches even with empty queue (user can sync or switch tabs)."""
        from unittest.mock import patch

        with patch("bb_review.ui.unified_app.UnifiedApp.run"):
            result = runner.invoke(main, ["--config", str(config_with_db), "interactive", "--queue"])
            assert result.exit_code == 0

    def test_interactive_queue_status_filter(self, runner: CliRunner, config_with_db: Path):
        from unittest.mock import patch

        with patch("bb_review.ui.unified_app.UnifiedApp.run"):
            result = runner.invoke(
                main,
                ["--config", str(config_with_db), "interactive", "--queue", "--queue-status", "todo"],
            )
            assert result.exit_code == 0

    def test_interactive_queue_invalid_status(self, runner: CliRunner, config_with_db: Path):
        result = runner.invoke(
            main, ["--config", str(config_with_db), "interactive", "--queue", "--queue-status", "bogus"]
        )
        assert result.exit_code != 0


class TestQueueProcess:
    def test_process_dry_run(self, runner: CliRunner, populated_queue: Path):
        result = runner.invoke(main, ["--config", str(populated_queue), "queue", "process", "--dry-run"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "42739" in result.output  # the 'next' item

    def test_process_nothing_to_do(self, runner: CliRunner, config_with_db: Path):
        result = runner.invoke(main, ["--config", str(config_with_db), "queue", "process"])
        assert result.exit_code == 0
        assert "No items with status=next" in result.output

    def test_process_resets_stale(
        self,
        runner: CliRunner,
        populated_queue: Path,
        queue_db: QueueDatabase,
    ):
        """Stale in_progress items are reset before picking."""
        queue_db.update_status(42739, QueueStatus.IN_PROGRESS)

        result = runner.invoke(main, ["--config", str(populated_queue), "queue", "process", "--dry-run"])
        assert result.exit_code == 0
        assert "Reset 1 stale" in result.output
