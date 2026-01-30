"""Tests for the init command."""

from pathlib import Path

from click.testing import CliRunner

from bb_review.cli import main


class TestInitCommand:
    """Tests for bb-review init command."""

    def test_init_creates_config(self, cli_runner: CliRunner, isolated_filesystem: Path):
        """Creates config.yaml in current directory."""
        result = cli_runner.invoke(main, ["init"])

        assert result.exit_code == 0
        assert (isolated_filesystem / "config.yaml").exists()
        assert "Created config file" in result.output

    def test_init_config_content(self, cli_runner: CliRunner, isolated_filesystem: Path):
        """Created config has expected structure."""
        cli_runner.invoke(main, ["init"])

        config_path = isolated_filesystem / "config.yaml"
        content = config_path.read_text()

        # Should have main sections
        assert "reviewboard:" in content
        assert "llm:" in content
        assert "repositories:" in content

    def test_init_no_overwrite_without_confirm(self, cli_runner: CliRunner, isolated_filesystem: Path):
        """Doesn't overwrite without confirmation."""
        # Create existing config
        config_path = isolated_filesystem / "config.yaml"
        config_path.write_text("# Existing config")

        # Run init and decline overwrite
        result = cli_runner.invoke(main, ["init"], input="n\n")

        # Should exit without overwriting
        assert "already exists" in result.output
        assert "# Existing config" in config_path.read_text()

    def test_init_overwrite_with_confirm(self, cli_runner: CliRunner, isolated_filesystem: Path):
        """Overwrites with confirmation."""
        # Create existing config
        config_path = isolated_filesystem / "config.yaml"
        config_path.write_text("# Existing config")

        # Run init and confirm overwrite
        result = cli_runner.invoke(main, ["init"], input="y\n")

        assert result.exit_code == 0
        # Should be overwritten
        assert "# Existing config" not in config_path.read_text()
        assert "reviewboard:" in config_path.read_text()

    def test_init_with_example_config(self, cli_runner: CliRunner, isolated_filesystem: Path):
        """Uses config.example.yaml if available."""
        # Note: The actual implementation looks for config.example.yaml
        # relative to the package, not cwd. This test verifies the fallback.
        result = cli_runner.invoke(main, ["init"])

        assert result.exit_code == 0
        # Should create config (even without example in cwd)
        assert (isolated_filesystem / "config.yaml").exists()

    def test_init_shows_next_steps(self, cli_runner: CliRunner, isolated_filesystem: Path):
        """Shows helpful next steps."""
        result = cli_runner.invoke(main, ["init"])

        assert "Next steps:" in result.output
        assert "Edit config.yaml" in result.output


class TestInitCommandHelp:
    """Tests for init command help."""

    def test_init_help(self, cli_runner: CliRunner):
        """Init help shows description."""
        result = cli_runner.invoke(main, ["init", "--help"])

        assert result.exit_code == 0
        assert "Initialize" in result.output
