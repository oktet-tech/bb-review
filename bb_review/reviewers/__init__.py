"""Review approaches for BB Review."""

from .claude_code import (
    ClaudeCodeError,
    ClaudeCodeNotFoundError,
    ClaudeCodeTimeoutError,
    check_claude_available,
    find_claude_binary,
    run_claude_review,
)
from .claude_code import build_review_prompt as build_claude_review_prompt
from .codex import (
    CodexError,
    CodexNotFoundError,
    CodexTimeoutError,
    check_codex_available,
    find_codex_binary,
    run_codex_review,
)
from .codex import build_review_prompt as build_codex_review_prompt
from .llm import (
    SYSTEM_PROMPT,
    Analyzer,
    extract_changed_files,
    filter_diff_by_paths,
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
from .providers import (
    AnthropicProvider,
    LLMProvider,
    OpenAIProvider,
    OpenRouterProvider,
    create_provider,
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
    # Claude Code reviewer
    "ClaudeCodeError",
    "ClaudeCodeNotFoundError",
    "ClaudeCodeTimeoutError",
    "build_claude_review_prompt",
    "check_claude_available",
    "find_claude_binary",
    "run_claude_review",
    # Codex reviewer
    "CodexError",
    "CodexNotFoundError",
    "CodexTimeoutError",
    "build_codex_review_prompt",
    "check_codex_available",
    "find_codex_binary",
    "run_codex_review",
]
