"""Tests for the submit command."""

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from bb_review.cli import main


class TestSubmitCommand:
    """Tests for bb-review submit command."""

    @pytest.fixture
    def valid_review_json(self, tmp_path: Path) -> Path:
        """Create a valid review JSON file."""
        review_data = {
            "review_request_id": 42738,
            "body_top": "**AI Review Complete**\n\nLooks good.",
            "comments": [
                {
                    "file_path": "src/main.c",
                    "line_number": 12,
                    "text": "Consider adding a comment here.",
                },
                {
                    "file_path": "src/utils.c",
                    "line_number": 5,
                    "text": "This could be simplified.",
                },
            ],
            "ship_it": False,
        }
        json_path = tmp_path / "review.json"
        json_path.write_text(json.dumps(review_data, indent=2))
        return json_path

    def test_submit_dry_run(
        self,
        cli_runner: CliRunner,
        valid_review_json: Path,
        temp_config_file: Path,
    ):
        """Dry run validates without posting."""
        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "submit", str(valid_review_json), "--dry-run"],
        )

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "#42738" in result.output
        assert "2" in result.output or "Comments: 2" in result.output

    def test_submit_shows_preview(
        self,
        cli_runner: CliRunner,
        valid_review_json: Path,
        temp_config_file: Path,
    ):
        """Dry run shows what would be posted."""
        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "submit", str(valid_review_json), "--dry-run"],
        )

        assert "Body Top" in result.output
        assert "src/main.c" in result.output

    def test_submit_invalid_json(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        temp_config_file: Path,
    ):
        """Error on malformed JSON."""
        invalid_json = tmp_path / "invalid.json"
        invalid_json.write_text("{ this is not valid json }")

        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "submit", str(invalid_json)],
        )

        assert result.exit_code == 1
        assert "Invalid JSON" in result.output

    def test_submit_missing_fields(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        temp_config_file: Path,
    ):
        """Error on missing required fields."""
        incomplete_json = tmp_path / "incomplete.json"
        incomplete_json.write_text(
            json.dumps(
                {
                    "review_request_id": 42738,
                    # Missing body_top and comments
                }
            )
        )

        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "submit", str(incomplete_json), "--dry-run"],
        )

        assert result.exit_code == 1
        assert "Missing required fields" in result.output

    def test_submit_invalid_comment(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        temp_config_file: Path,
    ):
        """Error on comment missing fields."""
        invalid_comment_json = tmp_path / "invalid_comment.json"
        invalid_comment_json.write_text(
            json.dumps(
                {
                    "review_request_id": 42738,
                    "body_top": "Test",
                    "comments": [
                        {
                            "file_path": "test.c",
                            # Missing line_number and text
                        }
                    ],
                }
            )
        )

        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "submit", str(invalid_comment_json), "--dry-run"],
        )

        assert result.exit_code == 1
        assert "missing required fields" in result.output.lower()

    def test_submit_empty_comments(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        temp_config_file: Path,
    ):
        """Empty comments array is valid."""
        review_json = tmp_path / "review.json"
        review_json.write_text(
            json.dumps(
                {
                    "review_request_id": 42738,
                    "body_top": "No issues found.",
                    "comments": [],
                }
            )
        )

        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "submit", str(review_json), "--dry-run"],
        )

        assert result.exit_code == 0

    def test_submit_with_ship_it(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        temp_config_file: Path,
    ):
        """Ship it flag is shown."""
        review_json = tmp_path / "review.json"
        review_json.write_text(
            json.dumps(
                {
                    "review_request_id": 42738,
                    "body_top": "LGTM",
                    "comments": [],
                    "ship_it": True,
                }
            )
        )

        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "submit", str(review_json), "--dry-run"],
        )

        assert result.exit_code == 0
        assert "Ship It: Yes" in result.output

    def test_submit_no_config_error(
        self,
        cli_runner: CliRunner,
        valid_review_json: Path,
        isolated_filesystem: Path,
    ):
        """Error without config file."""
        result = cli_runner.invoke(
            main,
            ["submit", str(valid_review_json)],
        )

        assert result.exit_code == 1
        assert "Config file required" in result.output


class TestSubmitHelp:
    """Tests for submit command help."""

    def test_submit_help(self, cli_runner: CliRunner):
        """Help shows description and arguments."""
        result = cli_runner.invoke(main, ["submit", "--help"])

        assert result.exit_code == 0
        assert "Submit" in result.output
        assert "JSON_FILE" in result.output
        assert "--dry-run" in result.output
