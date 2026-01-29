"""Review approaches for BB Review."""

from .llm import (
    Analyzer,
    SYSTEM_PROMPT,
    extract_changed_files,
    filter_diff_by_paths,
)
from .providers import (
    LLMProvider,
    AnthropicProvider,
    OpenRouterProvider,
    OpenAIProvider,
    create_provider,
)
from .opencode import (
    OpenCodeError,
    OpenCodeNotFoundError,
    OpenCodeTimeoutError,
    ParsedIssue,
    ParsedReview,
    build_review_prompt,
    check_opencode_available,
    find_opencode_binary,
    parse_opencode_output,
    run_opencode_agent,
    run_opencode_review,
)

__all__ = [
    # LLM reviewer
    "Analyzer",
    "SYSTEM_PROMPT",
    "extract_changed_files",
    "filter_diff_by_paths",
    # LLM providers
    "LLMProvider",
    "AnthropicProvider",
    "OpenRouterProvider",
    "OpenAIProvider",
    "create_provider",
    # OpenCode reviewer
    "OpenCodeError",
    "OpenCodeNotFoundError",
    "OpenCodeTimeoutError",
    "ParsedIssue",
    "ParsedReview",
    "build_review_prompt",
    "check_opencode_available",
    "find_opencode_binary",
    "parse_opencode_output",
    "run_opencode_agent",
    "run_opencode_review",
]
