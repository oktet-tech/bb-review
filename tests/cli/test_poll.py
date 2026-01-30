"""Tests for the poll subcommands."""

from pathlib import Path

from click.testing import CliRunner

from bb_review.cli import main


class TestPollStatus:
    """Tests for poll status command."""

    def test_poll_status_no_db(
        self,
        cli_runner: CliRunner,
        temp_config_file: Path,
    ):
        """Status works even with empty database."""
        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "poll", "status"],
        )

        # Should show status (even if empty)
        assert result.exit_code == 0
        assert "Polling Status" in result.output or "Status" in result.output

    def test_poll_status_shows_stats(
        self,
        cli_runner: CliRunner,
        temp_config_file: Path,
    ):
        """Shows statistics."""
        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "poll", "status"],
        )

        assert result.exit_code == 0
        # Should show some statistics
        assert "processed" in result.output.lower() or "total" in result.output.lower()


class TestPollOnce:
    """Tests for poll once command."""

    def test_poll_once_no_config(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Error without config."""
        result = cli_runner.invoke(
            main,
            ["poll", "once"],
        )

        assert result.exit_code == 1
        assert "Config file required" in result.output

    def test_poll_once_help(self, cli_runner: CliRunner):
        """Help shows description."""
        result = cli_runner.invoke(main, ["poll", "once", "--help"])

        assert result.exit_code == 0
        assert "poll" in result.output.lower()


class TestPollDaemon:
    """Tests for poll daemon command."""

    def test_poll_daemon_no_config(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Error without config."""
        result = cli_runner.invoke(
            main,
            ["poll", "daemon"],
        )

        assert result.exit_code == 1
        assert "Config file required" in result.output

    def test_poll_daemon_help(self, cli_runner: CliRunner):
        """Help shows description."""
        result = cli_runner.invoke(main, ["poll", "daemon", "--help"])

        assert result.exit_code == 0
        assert "daemon" in result.output.lower() or "polling" in result.output.lower()


class TestPollHelp:
    """Tests for poll command help."""

    def test_poll_help(self, cli_runner: CliRunner):
        """Shows subcommands."""
        result = cli_runner.invoke(main, ["poll", "--help"])

        assert result.exit_code == 0
        assert "once" in result.output
        assert "daemon" in result.output
        assert "status" in result.output

    def test_poll_status_help(self, cli_runner: CliRunner):
        """Status subcommand help."""
        result = cli_runner.invoke(main, ["poll", "status", "--help"])

        assert result.exit_code == 0
        assert "status" in result.output.lower() or "statistics" in result.output.lower()
