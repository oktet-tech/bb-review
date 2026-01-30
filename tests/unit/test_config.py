"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest

from bb_review.config import _resolve_env_var, load_config


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_from_path(self, valid_config_path: Path):
        """Load config from explicit path."""
        config = load_config(valid_config_path)

        assert config.reviewboard.url == "https://rb.example.com"
        assert config.reviewboard.api_token == "test-token-12345"
        assert config.reviewboard.bot_username == "test-bot"
        assert config.llm.provider == "anthropic"
        assert config.llm.model == "claude-sonnet-4-20250514"

    def test_load_config_default_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Search default paths when no path specified."""
        # Create config in current directory
        config_content = """
reviewboard:
  url: "https://rb.test.com"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "key"
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        # Change to directory with config
        monkeypatch.chdir(tmp_path)

        config = load_config()
        assert config.reviewboard.url == "https://rb.test.com"

    def test_load_config_file_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Error when config file not found."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config()

    def test_load_config_explicit_path_not_found(self, tmp_path: Path):
        """Error when explicit path doesn't exist."""
        fake_path = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            load_config(fake_path)


class TestEnvVarResolution:
    """Tests for environment variable resolution."""

    def test_env_var_resolution(self, monkeypatch: pytest.MonkeyPatch):
        """Resolve ${VAR} syntax."""
        monkeypatch.setenv("MY_VAR", "resolved-value")

        result = _resolve_env_var("${MY_VAR}")
        assert result == "resolved-value"

    def test_env_var_missing(self, monkeypatch: pytest.MonkeyPatch):
        """Error on missing env var."""
        monkeypatch.delenv("MISSING_VAR", raising=False)

        with pytest.raises(ValueError, match="Environment variable MISSING_VAR not set"):
            _resolve_env_var("${MISSING_VAR}")

    def test_plain_value_unchanged(self):
        """Plain values without ${} are unchanged."""
        result = _resolve_env_var("plain-value")
        assert result == "plain-value"

    def test_config_with_env_vars(self, env_config_path: Path, env_vars_for_config: None):
        """Load config with environment variable references."""
        config = load_config(env_config_path)

        assert config.reviewboard.api_token == "env-rb-token-value"
        assert config.llm.api_key == "env-llm-key-value"


class TestConfigValidation:
    """Tests for config validation."""

    def test_invalid_url(self, tmp_path: Path):
        """Reject URL without http(s)://."""
        config_content = """
reviewboard:
  url: "not-a-valid-url"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "key"
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        with pytest.raises(ValueError, match="URL must start with http"):
            load_config(config_path)

    def test_invalid_provider(self, tmp_path: Path):
        """Reject unknown LLM provider."""
        config_content = """
reviewboard:
  url: "https://rb.example.com"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "unknown-provider"
  model: "some-model"
  api_key: "key"
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        with pytest.raises(ValueError, match="Provider must be one of"):
            load_config(config_path)

    def test_url_trailing_slash_stripped(self, tmp_path: Path):
        """URL trailing slash is stripped."""
        config_content = """
reviewboard:
  url: "https://rb.example.com/"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "key"
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        config = load_config(config_path)
        assert config.reviewboard.url == "https://rb.example.com"


class TestConfigRepositories:
    """Tests for repository configuration."""

    def test_get_repo_by_name(self, valid_config_path: Path):
        """Find repo by name."""
        config = load_config(valid_config_path)

        repo = config.get_repo_by_name("test-repo")
        assert repo is not None
        assert repo.name == "test-repo"
        assert repo.rb_repo_name == "Test Repository"

    def test_get_repo_by_name_not_found(self, valid_config_path: Path):
        """Return None for unknown repo name."""
        config = load_config(valid_config_path)

        repo = config.get_repo_by_name("nonexistent")
        assert repo is None

    def test_get_repo_by_rb_name(self, valid_config_path: Path):
        """Find repo by RB name."""
        config = load_config(valid_config_path)

        repo = config.get_repo_by_rb_name("Test Repository")
        assert repo is not None
        assert repo.name == "test-repo"

    def test_get_repo_by_rb_name_not_found(self, valid_config_path: Path):
        """Return None for unknown RB name."""
        config = load_config(valid_config_path)

        repo = config.get_repo_by_rb_name("Unknown Repository")
        assert repo is None

    def test_get_all_repos(self, valid_config_path: Path):
        """Get all repository configs."""
        config = load_config(valid_config_path)

        repos = config.get_all_repos()
        assert len(repos) == 1
        assert repos[0].name == "test-repo"


class TestDefaultsConfig:
    """Tests for default review settings."""

    def test_focus_validation(self, tmp_path: Path):
        """Reject invalid focus area."""
        config_content = """
reviewboard:
  url: "https://rb.example.com"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "key"
defaults:
  focus:
    - bugs
    - invalid_focus
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        with pytest.raises(ValueError, match="Focus must be one of"):
            load_config(config_path)

    def test_severity_validation(self, tmp_path: Path):
        """Reject invalid severity threshold."""
        config_content = """
reviewboard:
  url: "https://rb.example.com"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "key"
defaults:
  severity_threshold: "invalid"
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        with pytest.raises(ValueError, match="Severity must be one of"):
            load_config(config_path)

    def test_default_values(self, tmp_path: Path):
        """Check default values when not specified."""
        config_content = """
reviewboard:
  url: "https://rb.example.com"
  api_token: "token"
  bot_username: "bot"
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-20250514"
  api_key: "key"
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)

        config = load_config(config_path)

        assert config.defaults.focus == ["bugs", "security"]
        assert config.defaults.severity_threshold == "medium"
        assert config.defaults.auto_ship_it is False
