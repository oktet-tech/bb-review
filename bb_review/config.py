"""Configuration loading and validation for BB Review."""

import os
from pathlib import Path
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator
import yaml

from .models import RepoConfig, ReviewFocus, Severity


class ReviewBoardConfig(BaseModel):
    """Review Board connection configuration."""

    url: str
    api_token: str | None = None
    username: str | None = None
    password: str | None = None
    password_file: str | None = None  # Path to encrypted password file
    encryption_token: str | None = None  # Token to decrypt password (defaults to api_token)
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
    def resolve_env_var(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _resolve_env_var(v)

    def get_password(self) -> str | None:
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
    base_url: str | None = None  # Custom API base URL
    site_url: str | None = None  # For OpenRouter rankings/analytics
    site_name: str | None = "BB Review"  # For OpenRouter rankings/analytics

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
    type: str | None = None  # e.g., "te-test-suite" for OpenCode MCP setup
    cocoindex: Optional["CocoIndexRepoConfig"] = None  # Per-repo CocoIndex settings

    def to_repo_config(self) -> RepoConfig:
        """Convert to RepoConfig dataclass."""
        return RepoConfig(
            name=self.name,
            local_path=Path(self.local_path).expanduser(),
            remote_url=self.remote_url,
            rb_repo_name=self.rb_repo_name,
            default_branch=self.default_branch,
            repo_type=self.type,
        )

    def is_cocoindex_enabled(self, global_enabled: bool = False) -> bool:
        """Check if CocoIndex is enabled for this repo."""
        if self.cocoindex:
            return self.cocoindex.enabled
        return global_enabled


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
    file: str | None = "~/.bb_review/bb_review.log"

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Level must be one of: {valid_levels}")
        return v

    @property
    def resolved_file(self) -> Path | None:
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


class OpenCodeConfig(BaseModel):
    """OpenCode agent configuration for alternative review mode."""

    enabled: bool = False
    model: str | None = None  # Override model for opencode (e.g., "anthropic/claude-sonnet-4-20250514")
    timeout: int = 300  # Timeout in seconds for opencode execution
    binary_path: str = "opencode"  # Path to the opencode binary


class CocoIndexRepoConfig(BaseModel):
    """Per-repository CocoIndex configuration."""

    enabled: bool = False


class ReviewDBConfig(BaseModel):
    """Reviews database configuration for storing analysis history."""

    enabled: bool = False
    path: str = "~/.bb_review/reviews.db"

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser()


class CocoIndexConfig(BaseModel):
    """Global CocoIndex configuration for semantic code indexing.

    Uses CocoIndex with local sentence-transformers embeddings.
    Requires PostgreSQL with pgvector extension.
    No API keys needed - embeddings run locally.
    """

    enabled: bool = False
    database_url: str = "postgresql://cocoindex:cocoindex@localhost:5432/cocoindex"
    log_dir: str = "~/.bb_review/cocoindex"  # Directory for CocoIndex logs
    # Local embedding model from HuggingFace sentence-transformers
    # Popular options:
    #   - sentence-transformers/all-MiniLM-L6-v2 (fast, good quality)
    #   - sentence-transformers/all-mpnet-base-v2 (better quality, slower)
    #   - BAAI/bge-small-en-v1.5 (good for code)
    #   - nomic-ai/nomic-embed-text-v1.5 (good general purpose)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_size: int = 1000  # Characters per chunk
    chunk_overlap: int = 300  # Overlap between chunks
    # File patterns to include (defaults to common code extensions)
    included_patterns: list[str] | None = None
    # File patterns to exclude (defaults to common excludes like .git, node_modules)
    excluded_patterns: list[str] | None = None

    @field_validator("database_url")
    @classmethod
    def resolve_env_var(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _resolve_env_var(v)

    @field_validator("embedding_model")
    @classmethod
    def validate_embedding_model(cls, v: str) -> str:
        # Basic validation - model name should look reasonable
        if not v or "/" not in v:
            raise ValueError(
                "embedding_model should be a HuggingFace model like 'sentence-transformers/all-MiniLM-L6-v2'"
            )
        return v

    # Keep old validator for backwards compatibility - migrate old provider names
    @field_validator("embedding_model", mode="before")
    @classmethod
    def migrate_old_provider(cls, v: str) -> str:
        # If someone has old provider-based config, migrate to default local model
        old_providers = ["jina", "lmstudio", "openai", "mistral", "openrouter"]
        if v in old_providers:
            import logging

            logging.getLogger(__name__).warning(
                f"Old embedding_provider '{v}' detected. Migrating to local sentence-transformers model."
            )
            return "sentence-transformers/all-MiniLM-L6-v2"
        return v

    @property
    def resolved_log_dir(self) -> Path:
        return Path(self.log_dir).expanduser()


class Config(BaseModel):
    """Main configuration model."""

    reviewboard: ReviewBoardConfig
    llm: LLMConfig
    repositories: list[RepositoryConfig] = Field(default_factory=list)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    cocoindex: CocoIndexConfig = Field(default_factory=CocoIndexConfig)
    review_db: ReviewDBConfig = Field(default_factory=ReviewDBConfig)

    def get_repo_by_name(self, name: str) -> RepoConfig | None:
        """Get repository config by name."""
        for repo in self.repositories:
            if repo.name == name:
                return repo.to_repo_config()
        return None

    def get_repo_by_rb_name(self, rb_name: str) -> RepoConfig | None:
        """Get repository config by Review Board repository name."""
        for repo in self.repositories:
            if repo.rb_repo_name == rb_name:
                return repo.to_repo_config()
        return None

    def get_all_repos(self) -> list[RepoConfig]:
        """Get all repository configs."""
        return [repo.to_repo_config() for repo in self.repositories]

    def get_repo_config_by_name(self, name: str) -> RepositoryConfig | None:
        """Get repository config (Pydantic model) by name."""
        for repo in self.repositories:
            if repo.name == name:
                return repo
        return None

    def get_cocoindex_enabled_repos(self) -> list[RepositoryConfig]:
        """Get all repositories with CocoIndex enabled."""
        return [repo for repo in self.repositories if repo.is_cocoindex_enabled(self.cocoindex.enabled)]


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


def load_config(config_path: Path | None = None) -> Config:
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
            raise FileNotFoundError(f"Config file not found. Searched: {[str(p) for p in search_paths]}")

    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    return Config.model_validate(raw_config)


def ensure_directories(config: Config) -> None:
    """Ensure required directories exist."""
    # State database directory
    db_path = config.database.resolved_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Reviews database directory
    if config.review_db.enabled:
        review_db_path = config.review_db.resolved_path
        review_db_path.parent.mkdir(parents=True, exist_ok=True)

    # Log directory
    if config.logging.resolved_file:
        config.logging.resolved_file.parent.mkdir(parents=True, exist_ok=True)


# Global config instance (set by CLI)
_config: Config | None = None


def get_config() -> Config:
    """Get the global config instance."""
    if _config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    return _config


def set_config(config: Config) -> None:
    """Set the global config instance."""
    global _config
    _config = config
