"""Tests for the db CLI commands."""

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from bb_review.cli import main


@pytest.fixture
def runner() -> CliRunner:
    """Create a Click CLI runner."""
    return CliRunner()


@pytest.fixture
def config_with_db(tmp_path: Path) -> Path:
    """Create a config file with review_db enabled."""
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
def config_without_db(tmp_path: Path) -> Path:
    """Create a config file without review_db enabled."""
    config_content = """
reviewboard:
  url: "https://rb.example.com"
  api_token: "test-token"
  bot_username: "ai-reviewer"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "test-key"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def db_with_data(config_with_db: Path, tmp_path: Path) -> Path:
    """Create a database with sample data."""
    from bb_review.db import ReviewDatabase
    from bb_review.models import ReviewComment, ReviewFocus, ReviewResult, Severity

    # Get db path from config
    db_path = tmp_path / "reviews.db"
    db = ReviewDatabase(db_path)

    # Add sample data
    result = ReviewResult(
        review_request_id=42738,
        diff_revision=1,
        comments=[
            ReviewComment(
                file_path="src/main.c",
                line_number=42,
                message="Test issue",
                severity=Severity.MEDIUM,
                issue_type=ReviewFocus.BUGS,
                suggestion="Fix it",
            ),
        ],
        summary="Found 1 issue",
    )
    db.save_analysis(
        result=result,
        repository="test-repo",
        analysis_method="llm",
        model="claude-sonnet-4",
    )

    return config_with_db


class TestDbList:
    """Tests for 'bb-review db list' command."""

    def test_db_list_empty(self, runner: CliRunner, config_with_db: Path):
        """List when database is empty."""
        result = runner.invoke(main, ["--config", str(config_with_db), "db", "list"])

        assert result.exit_code == 0
        assert "No analyses found" in result.output

    def test_db_list_with_data(self, runner: CliRunner, db_with_data: Path):
        """List with data in database."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "list"])

        assert result.exit_code == 0
        assert "Found 1 analyses" in result.output
        assert "RR #42738" in result.output
        assert "test-repo" in result.output

    def test_db_list_filter_rr(self, runner: CliRunner, db_with_data: Path):
        """Filter by review request ID."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "list", "--rr", "42738"])

        assert result.exit_code == 0
        assert "RR #42738" in result.output

    def test_db_list_filter_repo(self, runner: CliRunner, db_with_data: Path):
        """Filter by repository."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "list", "--repo", "test-repo"])

        assert result.exit_code == 0
        assert "test-repo" in result.output

    def test_db_not_enabled(self, runner: CliRunner, config_without_db: Path):
        """Error when review_db is not enabled."""
        result = runner.invoke(main, ["--config", str(config_without_db), "db", "list"])

        assert result.exit_code == 1
        assert "Reviews database is not enabled" in result.output


class TestDbShow:
    """Tests for 'bb-review db show' command."""

    def test_db_show(self, runner: CliRunner, db_with_data: Path):
        """Show analysis details."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "show", "1"])

        assert result.exit_code == 0
        assert "Analysis #1" in result.output
        assert "Review Request: #42738" in result.output
        assert "Repository:     test-repo" in result.output
        assert "Found 1 issue" in result.output
        assert "src/main.c:42" in result.output

    def test_db_show_not_found(self, runner: CliRunner, config_with_db: Path):
        """Error when analysis not found."""
        result = runner.invoke(main, ["--config", str(config_with_db), "db", "show", "999"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_db_show_no_comments(self, runner: CliRunner, db_with_data: Path):
        """Show without comments."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "show", "1", "--no-comments"])

        assert result.exit_code == 0
        assert "Analysis #1" in result.output
        # Comments section should not appear
        assert "src/main.c:42" not in result.output


class TestDbExport:
    """Tests for 'bb-review db export' command."""

    def test_db_export_json(self, runner: CliRunner, db_with_data: Path):
        """Export to JSON format."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "export", "1"])

        assert result.exit_code == 0

        # Parse output as JSON
        data = json.loads(result.output)
        assert data["review_request_id"] == 42738
        assert "body_top" in data
        assert "comments" in data
        assert len(data["comments"]) == 1

    def test_db_export_json_file(self, runner: CliRunner, db_with_data: Path, tmp_path: Path):
        """Export to JSON file."""
        output_file = tmp_path / "export.json"
        result = runner.invoke(
            main,
            ["--config", str(db_with_data), "db", "export", "1", "-o", str(output_file)],
        )

        assert result.exit_code == 0
        assert output_file.exists()

        data = json.loads(output_file.read_text())
        assert data["review_request_id"] == 42738

    def test_db_export_markdown(self, runner: CliRunner, db_with_data: Path):
        """Export to Markdown format."""
        result = runner.invoke(
            main, ["--config", str(db_with_data), "db", "export", "1", "--format", "markdown"]
        )

        assert result.exit_code == 0
        assert "# Code Review: RR #42738" in result.output
        assert "**Repository**: test-repo" in result.output

    def test_db_export_not_found(self, runner: CliRunner, config_with_db: Path):
        """Error when analysis not found."""
        result = runner.invoke(main, ["--config", str(config_with_db), "db", "export", "999"])

        assert result.exit_code == 1
        assert "not found" in result.output


class TestDbStats:
    """Tests for 'bb-review db stats' command."""

    def test_db_stats_empty(self, runner: CliRunner, config_with_db: Path):
        """Show stats for empty database."""
        result = runner.invoke(main, ["--config", str(config_with_db), "db", "stats"])

        assert result.exit_code == 0
        assert "Total Analyses: 0" in result.output
        assert "Total Comments: 0" in result.output

    def test_db_stats_with_data(self, runner: CliRunner, db_with_data: Path):
        """Show stats with data."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "stats"])

        assert result.exit_code == 0
        assert "Total Analyses: 1" in result.output
        assert "Total Comments: 1" in result.output
        assert "By Repository:" in result.output
        assert "test-repo: 1" in result.output


class TestDbMark:
    """Tests for 'bb-review db mark' command."""

    def test_db_mark_submitted(self, runner: CliRunner, db_with_data: Path):
        """Mark analysis as submitted."""
        result = runner.invoke(
            main, ["--config", str(db_with_data), "db", "mark", "1", "--status", "submitted"]
        )

        assert result.exit_code == 0
        assert "draft -> submitted" in result.output

    def test_db_mark_abandoned(self, runner: CliRunner, db_with_data: Path):
        """Mark analysis as abandoned."""
        result = runner.invoke(
            main, ["--config", str(db_with_data), "db", "mark", "1", "--status", "abandoned"]
        )

        assert result.exit_code == 0
        assert "draft -> abandoned" in result.output

    def test_db_mark_not_found(self, runner: CliRunner, config_with_db: Path):
        """Error when analysis not found."""
        result = runner.invoke(
            main, ["--config", str(config_with_db), "db", "mark", "999", "--status", "submitted"]
        )

        assert result.exit_code == 1
        assert "not found" in result.output


class TestDbSearch:
    """Tests for 'bb-review db search' command."""

    def test_db_search_by_rr_id(self, runner: CliRunner, db_with_data: Path):
        """Search by review request ID."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "search", "42738"])

        assert result.exit_code == 0
        assert "RR #42738" in result.output

    def test_db_search_by_text(self, runner: CliRunner, db_with_data: Path):
        """Search by summary text."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "search", "issue"])

        assert result.exit_code == 0
        assert "Found 1 analyses" in result.output

    def test_db_search_no_results(self, runner: CliRunner, db_with_data: Path):
        """Search with no matches."""
        result = runner.invoke(main, ["--config", str(db_with_data), "db", "search", "nonexistent"])

        assert result.exit_code == 0
        assert "No analyses found" in result.output


class TestDbCleanup:
    """Tests for 'bb-review db cleanup' command."""

    def test_db_cleanup_dry_run(self, runner: CliRunner, db_with_data: Path):
        """Cleanup dry run."""
        result = runner.invoke(
            main,
            ["--config", str(db_with_data), "db", "cleanup", "--older-than", "0", "--dry-run"],
        )

        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_db_cleanup_nothing_to_delete(self, runner: CliRunner, db_with_data: Path):
        """Cleanup with nothing old enough."""
        result = runner.invoke(
            main,
            ["--config", str(db_with_data), "db", "cleanup", "--older-than", "365"],
        )

        assert result.exit_code == 0
        assert "No analyses older than 365 days found" in result.output
