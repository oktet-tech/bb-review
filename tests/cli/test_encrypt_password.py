"""Tests for the encrypt-password command."""

from pathlib import Path

from click.testing import CliRunner

from bb_review.cli import main
from bb_review.crypto import decrypt_password_from_file


class TestEncryptPasswordCommand:
    """Tests for bb-review encrypt-password command."""

    def test_encrypt_password_success(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Encrypts and saves password."""
        output_path = isolated_filesystem / "password.enc"

        result = cli_runner.invoke(
            main,
            ["encrypt-password", "--token", "test-token", "--output", str(output_path)],
            input="mypassword\nmypassword\n",  # password + confirmation
        )

        assert result.exit_code == 0
        assert output_path.exists()
        assert "Password encrypted and saved" in result.output

        # Verify we can decrypt
        decrypted = decrypt_password_from_file(output_path, "test-token")
        assert decrypted == "mypassword"

    def test_encrypt_password_mismatch(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Error on password mismatch."""
        output_path = isolated_filesystem / "password.enc"

        result = cli_runner.invoke(
            main,
            ["encrypt-password", "--token", "test-token", "--output", str(output_path)],
            input="password1\npassword2\n",  # different passwords
        )

        assert result.exit_code == 1
        assert "don't match" in result.output
        assert not output_path.exists()

    def test_encrypt_password_no_token(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Error without token and no config."""
        result = cli_runner.invoke(
            main,
            ["encrypt-password"],
            input="password\npassword\n",
        )

        # Should fail because no token provided and no config to get api_token
        assert result.exit_code == 1
        assert "No --token provided" in result.output or "couldn't load config" in result.output

    def test_encrypt_password_with_config(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Uses api_token from config if available."""
        # Create config with api_token
        config_content = """
reviewboard:
  url: "https://rb.example.com"
  api_token: "config-token-12345"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "test"
  api_key: "test-key"
"""
        config_path = isolated_filesystem / "config.yaml"
        config_path.write_text(config_content)

        output_path = isolated_filesystem / "password.enc"

        result = cli_runner.invoke(
            main,
            ["encrypt-password", "--output", str(output_path)],
            input="mypassword\nmypassword\n",
        )

        assert result.exit_code == 0
        assert "Using api_token from config" in result.output

        # Verify we can decrypt with config token
        decrypted = decrypt_password_from_file(output_path, "config-token-12345")
        assert decrypted == "mypassword"

    def test_encrypt_password_creates_directories(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Creates parent directories if needed."""
        output_path = isolated_filesystem / "nested" / "dir" / "password.enc"

        result = cli_runner.invoke(
            main,
            ["encrypt-password", "--token", "test-token", "--output", str(output_path)],
            input="password\npassword\n",
        )

        assert result.exit_code == 0
        assert output_path.exists()

    def test_encrypt_password_file_permissions(
        self,
        cli_runner: CliRunner,
        isolated_filesystem: Path,
    ):
        """Encrypted file has secure permissions."""
        output_path = isolated_filesystem / "password.enc"

        cli_runner.invoke(
            main,
            ["encrypt-password", "--token", "test-token", "--output", str(output_path)],
            input="password\npassword\n",
        )

        # Check permissions are 0600
        mode = output_path.stat().st_mode & 0o777
        assert mode == 0o600


class TestEncryptPasswordHelp:
    """Tests for encrypt-password command help."""

    def test_encrypt_password_help(self, cli_runner: CliRunner):
        """Help shows description and options."""
        result = cli_runner.invoke(main, ["encrypt-password", "--help"])

        assert result.exit_code == 0
        assert "Encrypt" in result.output
        assert "--token" in result.output
        assert "--output" in result.output
