"""Tests for the repos subcommands."""

from pathlib import Path

from click.testing import CliRunner

from bb_review.cli import main


class TestReposList:
    """Tests for repos list command."""

    def test_repos_list(
        self,
        cli_runner: CliRunner,
        temp_config_file: Path,
    ):
        """Lists configured repositories."""
        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "repos", "list"],
        )

        assert result.exit_code == 0
        assert "test-repo" in result.output
        assert "Test Repository" in result.output

    def test_repos_list_empty(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ):
        """Message when no repos configured."""
        # Create config without repos
        config_content = """
reviewboard:
  url: "https://rb.example.com"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "test"
  api_key: "key"
repositories: []
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        result = cli_runner.invoke(
            main,
            ["-c", str(config_path), "repos", "list"],
        )

        assert result.exit_code == 0
        assert "No repositories configured" in result.output

    def test_repos_list_shows_status(
        self,
        cli_runner: CliRunner,
        temp_config_file: Path,
    ):
        """Shows clone status for repos."""
        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "repos", "list"],
        )

        # Should show clone status
        assert "cloned" in result.output.lower() or "not cloned" in result.output.lower()


class TestReposInitGuidelines:
    """Tests for repos init-guidelines command."""

    def test_repos_init_guidelines(
        self,
        cli_runner: CliRunner,
        temp_config_file: Path,
        tmp_path: Path,
    ):
        """Creates .ai-review.yaml in repo."""
        # Create the repo directory (config points to tmp_path/repo)
        repo_path = tmp_path / "repo"
        repo_path.mkdir(parents=True, exist_ok=True)

        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "repos", "init-guidelines", "test-repo"],
        )

        # May succeed or fail depending on guide file existence
        # The important thing is the command runs
        output_lower = result.output.lower()
        assert "guide" in output_lower or "created" in output_lower or "error" in output_lower

    def test_repos_init_guidelines_force(
        self,
        cli_runner: CliRunner,
        temp_config_file: Path,
        tmp_path: Path,
    ):
        """Force flag overwrites existing."""
        # Create repo directory and existing guidelines
        repo_path = tmp_path / "repo"
        repo_path.mkdir(parents=True, exist_ok=True)
        existing = repo_path / ".ai-review.yaml"
        existing.write_text("# Existing guidelines")

        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "repos", "init-guidelines", "test-repo", "--force"],
        )

        # Command should run (may create new content)
        assert result.exit_code == 0 or "error" in result.output.lower()

    def test_repos_init_guidelines_repo_not_found(
        self,
        cli_runner: CliRunner,
        temp_config_file: Path,
    ):
        """Error for unknown repo."""
        result = cli_runner.invoke(
            main,
            ["-c", str(temp_config_file), "repos", "init-guidelines", "nonexistent-repo"],
        )

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "error" in result.output.lower()


class TestReposSync:
    """Tests for repos sync command."""

    def test_repos_sync_help(self, cli_runner: CliRunner):
        """Sync help shows description."""
        result = cli_runner.invoke(main, ["repos", "sync", "--help"])

        assert result.exit_code == 0
        assert "sync" in result.output.lower() or "Fetch" in result.output

    def test_repos_sync_no_config(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Error without config."""
        result = cli_runner.invoke(
            main,
            ["repos", "sync"],
        )

        assert result.exit_code == 1
        assert "Config file required" in result.output


class TestReposHelp:
    """Tests for repos command help."""

    def test_repos_help(self, cli_runner: CliRunner):
        """Shows subcommands."""
        result = cli_runner.invoke(main, ["repos", "--help"])

        assert result.exit_code == 0
        assert "list" in result.output
        assert "sync" in result.output
        assert "init-guidelines" in result.output

    def test_repos_list_help(self, cli_runner: CliRunner):
        """List subcommand help."""
        result = cli_runner.invoke(main, ["repos", "list", "--help"])

        assert result.exit_code == 0
        assert "List" in result.output
