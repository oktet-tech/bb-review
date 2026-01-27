"""Configuration loading and validation for BB Review."""

import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .models import RepoConfig, ReviewFocus, Severity


class ReviewBoardConfig(BaseModel):
    """Review Board connection configuration."""

    url: str
    api_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    password_file: Optional[str] = None  # Path to encrypted password file
    encryption_token: Optional[str] = None  # Token to decrypt password (defaults to api_token)
    use_kerberos: bool = False  # Use Kerberos/Negotiate authentication
    bot_username: str = "ai-reviewer"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v.rstrip("/")

    @field_validator("api_token", "password", "encryption_token")
    @classmethod
    def resolve_env_var(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _resolve_env_var(v)

    def get_password(self) -> Optional[str]:
        """Get the password, decrypting from file if needed."""
        # Direct password takes precedence
        if self.password:
            return self.password
        
        # Try password file
        if self.password_file:
            from pathlib import Path
            from .crypto import decrypt_password_from_file
            
            file_path = Path(self.password_file).expanduser()
            token = self.encryption_token or self.api_token
            if not token:
                raise ValueError("encryption_token or api_token required to decrypt password_file")
            
            return decrypt_password_from_file(file_path, token)
        
        return None


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: str
    max_tokens: int = 4096
    temperature: float = 0.2
    # OpenRouter-specific settings
    base_url: Optional[str] = None  # Custom API base URL
    site_url: Optional[str] = None  # For OpenRouter rankings/analytics
    site_name: Optional[str] = "BB Review"  # For OpenRouter rankings/analytics

    @field_validator("api_key")
    @classmethod
    def resolve_env_var(cls, v: str) -> str:
        return _resolve_env_var(v)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        supported = ["anthropic", "openrouter", "openai"]
        if v not in supported:
            raise ValueError(f"Provider must be one of: {supported}")
        return v


class RepositoryConfig(BaseModel):
    """Single repository configuration."""

    name: str
    rb_repo_name: str
    local_path: str
    remote_url: str
    default_branch: str = "main"

    def to_repo_config(self) -> RepoConfig:
        """Convert to RepoConfig dataclass."""
        return RepoConfig(
            name=self.name,
            local_path=Path(self.local_path).expanduser(),
            remote_url=self.remote_url,
            rb_repo_name=self.rb_repo_name,
            default_branch=self.default_branch,
        )


class PollingConfig(BaseModel):
    """Polling configuration."""

    interval_seconds: int = 300
    max_reviews_per_cycle: int = 10


class DatabaseConfig(BaseModel):
    """Database configuration."""

    path: str = "~/.bb_review/state.db"

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser()


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"
    file: Optional[str] = "~/.bb_review/bb_review.log"

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Level must be one of: {valid_levels}")
        return v

    @property
    def resolved_file(self) -> Optional[Path]:
        if self.file:
            return Path(self.file).expanduser()
        return None


class DefaultsConfig(BaseModel):
    """Default review settings."""

    focus: list[str] = Field(default_factory=lambda: ["bugs", "security"])
    severity_threshold: str = "medium"
    auto_ship_it: bool = False

    @field_validator("focus")
    @classmethod
    def validate_focus(cls, v: list[str]) -> list[str]:
        valid_focus = [f.value for f in ReviewFocus]
        for item in v:
            if item not in valid_focus:
                raise ValueError(f"Focus must be one of: {valid_focus}")
        return v

    @field_validator("severity_threshold")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        valid_severities = [s.value for s in Severity]
        if v not in valid_severities:
            raise ValueError(f"Severity must be one of: {valid_severities}")
        return v

    def get_focus_enums(self) -> list[ReviewFocus]:
        return [ReviewFocus(f) for f in self.focus]

    def get_severity_enum(self) -> Severity:
        return Severity(self.severity_threshold)


class Config(BaseModel):
    """Main configuration model."""

    reviewboard: ReviewBoardConfig
    llm: LLMConfig
    repositories: list[RepositoryConfig] = Field(default_factory=list)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)

    def get_repo_by_name(self, name: str) -> Optional[RepoConfig]:
        """Get repository config by name."""
        for repo in self.repositories:
            if repo.name == name:
                return repo.to_repo_config()
        return None

    def get_repo_by_rb_name(self, rb_name: str) -> Optional[RepoConfig]:
        """Get repository config by Review Board repository name."""
        for repo in self.repositories:
            if repo.rb_repo_name == rb_name:
                return repo.to_repo_config()
        return None

    def get_all_repos(self) -> list[RepoConfig]:
        """Get all repository configs."""
        return [repo.to_repo_config() for repo in self.repositories]


def _resolve_env_var(value: str) -> str:
    """Resolve environment variable references in config values.

    Supports ${VAR_NAME} syntax.
    """
    pattern = r"\$\{([^}]+)\}"
    match = re.match(pattern, value)
    if match:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable {var_name} not set")
        return env_value
    return value


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file. If None, searches in default locations.

    Returns:
        Validated Config object.

    Raises:
        FileNotFoundError: If config file not found.
        ValueError: If config validation fails.
    """
    if config_path is None:
        # Search in default locations
        search_paths = [
            Path.cwd() / "config.yaml",
            Path.home() / ".bb_review" / "config.yaml",
            Path("/etc/bb_review/config.yaml"),
        ]
        for path in search_paths:
            if path.exists():
                config_path = path
                break
        else:
            raise FileNotFoundError(
                f"Config file not found. Searched: {[str(p) for p in search_paths]}"
            )

    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    return Config.model_validate(raw_config)


def ensure_directories(config: Config) -> None:
    """Ensure required directories exist."""
    # Database directory
    db_path = config.database.resolved_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Log directory
    if config.logging.resolved_file:
        config.logging.resolved_file.parent.mkdir(parents=True, exist_ok=True)


# Global config instance (set by CLI)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global config instance."""
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    return _config


def set_config(config: Config) -> None:
    """Set the global config instance."""
    global _config
    _config = config
