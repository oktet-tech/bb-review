"""AI-powered code analyzer using LLM for code review."""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

import anthropic
import openai

from .models import ReviewComment, ReviewFocus, ReviewGuidelines, ReviewResult, Severity

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert code reviewer. Your task is to analyze code changes (diffs) and provide actionable, specific feedback.

Guidelines for your review:
1. Focus on substantive issues, not style nitpicks (unless style is specifically requested)
2. Be specific - reference exact line numbers and code
3. Explain WHY something is a problem, not just WHAT is wrong
4. Suggest concrete fixes when possible
5. Prioritize issues by severity (critical > high > medium > low)
6. Don't comment on things that are clearly intentional or already follow best practices
7. Consider the broader context of the codebase when making suggestions

Your response must be valid JSON matching this schema:
{
  "summary": "Brief overall assessment of the changes",
  "has_critical_issues": true/false,
  "comments": [
    {
      "file_path": "path/to/file.ext",
      "line_number": 42,
      "severity": "low|medium|high|critical",
      "issue_type": "bugs|security|performance|style|architecture",
      "message": "Clear explanation of the issue",
      "suggestion": "Suggested fix or improvement (optional)"
    }
  ]
}

Important:
- line_number should reference lines in the NEW version of the file (lines starting with + in the diff)
- Only comment on actual issues - if the code is good, return an empty comments array
- Be constructive and professional in tone"""


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a completion request to the LLM.

        Args:
            system_prompt: System prompt.
            user_prompt: User prompt.

        Returns:
            Response text.
        """
        pass


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text


class OpenRouterProvider(LLMProvider):
    """OpenRouter provider (OpenAI-compatible API)."""

    def __init__(
        self,
        api_key: str,
        model: str = "anthropic/claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        temperature: float = 0.2,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: Optional[str] = None,
        site_name: str = "BB Review",
    ):
        extra_headers = {
            "HTTP-Referer": site_url or "",
            "X-Title": site_name,
        }
        # Remove empty headers
        extra_headers = {k: v for k, v in extra_headers.items() if v}

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=extra_headers,
        )
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""


class OpenAIProvider(LLMProvider):
    """OpenAI provider."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.2,
        base_url: Optional[str] = None,
    ):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url

        self.client = openai.OpenAI(**kwargs)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""


def create_provider(
    provider: str,
    api_key: str,
    model: str,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    base_url: Optional[str] = None,
    site_url: Optional[str] = None,
    site_name: str = "BB Review",
) -> LLMProvider:
    """Factory function to create an LLM provider.

    Args:
        provider: Provider name ("anthropic", "openrouter", "openai").
        api_key: API key for the provider.
        model: Model name.
        max_tokens: Maximum tokens in response.
        temperature: Sampling temperature.
        base_url: Custom base URL (for openrouter/openai).
        site_url: Site URL for OpenRouter analytics.
        site_name: Site name for OpenRouter analytics.

    Returns:
        LLMProvider instance.
    """
    if provider == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    elif provider == "openrouter":
        return OpenRouterProvider(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            base_url=base_url or "https://openrouter.ai/api/v1",
            site_url=site_url,
            site_name=site_name,
        )
    elif provider == "openai":
        return OpenAIProvider(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            base_url=base_url,
        )
    else:
        raise ValueError(f"Unknown provider: {provider}")


class Analyzer:
    """Analyzes code changes using an LLM."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        temperature: float = 0.2,
        provider: str = "anthropic",
        base_url: Optional[str] = None,
        site_url: Optional[str] = None,
        site_name: str = "BB Review",
    ):
        """Initialize the analyzer.

        Args:
            api_key: API key for the LLM provider.
            model: Model to use.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            provider: LLM provider ("anthropic", "openrouter", "openai").
            base_url: Custom base URL for OpenRouter/OpenAI.
            site_url: Site URL for OpenRouter analytics.
            site_name: Site name for OpenRouter analytics.
        """
        self.provider_name = provider
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

        self.llm = create_provider(
            provider=provider,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            base_url=base_url,
            site_url=site_url,
            site_name=site_name,
        )

    def analyze(
        self,
        diff: str,
        guidelines: ReviewGuidelines,
        file_contexts: Optional[dict[str, str]] = None,
        review_request_id: int = 0,
        diff_revision: int = 0,
    ) -> ReviewResult:
        """Analyze a diff and return review comments.

        Args:
            diff: The raw diff content.
            guidelines: Review guidelines from the repository.
            file_contexts: Optional dict mapping file paths to surrounding context.
            review_request_id: Review request ID for the result.
            diff_revision: Diff revision number.

        Returns:
            ReviewResult with comments.
        """
        prompt = self._build_prompt(diff, guidelines, file_contexts)

        logger.info(
            f"Analyzing diff ({len(diff)} chars) with {self.provider_name}/{self.model}"
        )
        logger.debug(f"Prompt length: {len(prompt)} chars")

        try:
            result_text = self.llm.complete(SYSTEM_PROMPT, prompt)
            logger.debug(f"Raw response: {result_text[:500]}...")
            
            # Store raw response for debugging
            self._last_raw_response = result_text

            return self._parse_response(result_text, review_request_id, diff_revision)

        except (anthropic.APIError, openai.APIError) as e:
            logger.error(f"API error during analysis: {e}")
            raise
        except Exception as e:
            logger.error(f"Error during analysis: {e}")
            raise
    
    def get_last_raw_response(self) -> Optional[str]:
        """Get the raw response from the last analysis."""
        return getattr(self, "_last_raw_response", None)

    def _build_prompt(
        self,
        diff: str,
        guidelines: ReviewGuidelines,
        file_contexts: Optional[dict[str, str]] = None,
    ) -> str:
        """Build the prompt for the LLM.

        Args:
            diff: The raw diff content.
            guidelines: Review guidelines.
            file_contexts: Optional file context.

        Returns:
            Formatted prompt string.
        """
        parts = []

        # Add focus areas
        focus_list = ", ".join(f.value for f in guidelines.focus)
        parts.append(f"## Review Focus\nFocus on these issue types: {focus_list}")

        # Add severity threshold
        parts.append(
            f"\n## Severity Threshold\n"
            f"Only report issues at {guidelines.severity_threshold.value} severity or higher."
        )

        # Add custom context if provided
        if guidelines.context:
            parts.append(f"\n## Repository Context\n{guidelines.context}")

        # Add custom rules
        if guidelines.custom_rules:
            rules = "\n".join(f"- {rule}" for rule in guidelines.custom_rules)
            parts.append(f"\n## Custom Rules\n{rules}")

        # Add ignore paths note
        if guidelines.ignore_paths:
            ignore_list = ", ".join(guidelines.ignore_paths)
            parts.append(
                f"\n## Ignore Paths\n"
                f"Do not comment on files matching: {ignore_list}"
            )

        # Add file context if provided
        if file_contexts:
            parts.append("\n## File Context\nHere is surrounding context for the modified files:")
            for file_path, context in file_contexts.items():
                parts.append(f"\n### {file_path}\n```\n{context}\n```")

        # Add the diff
        parts.append(f"\n## Diff to Review\n```diff\n{diff}\n```")

        # Final instruction
        parts.append(
            "\n## Instructions\n"
            "Analyze the diff above and provide your review as JSON. "
            "Remember to only include substantive issues and be specific with line numbers."
        )

        return "\n".join(parts)

    def _parse_response(
        self, response_text: str, review_request_id: int, diff_revision: int
    ) -> ReviewResult:
        """Parse the LLM response into a ReviewResult.

        Args:
            response_text: Raw response text from LLM.
            review_request_id: Review request ID.
            diff_revision: Diff revision number.

        Returns:
            Parsed ReviewResult.
        """
        # Try to extract JSON from the response
        json_match = re.search(r"\{[\s\S]*\}", response_text)
        if not json_match:
            logger.warning("Could not find JSON in response")
            logger.debug(f"Raw LLM response:\n{response_text[:2000]}")
            return ReviewResult(
                review_request_id=review_request_id,
                diff_revision=diff_revision,
                comments=[],
                summary="Failed to parse review response",
            )

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON: {e}")
            return ReviewResult(
                review_request_id=review_request_id,
                diff_revision=diff_revision,
                comments=[],
                summary="Failed to parse review response",
            )

        # Parse comments
        comments = []
        for c in data.get("comments", []):
            try:
                comment = ReviewComment(
                    file_path=c["file_path"],
                    line_number=int(c["line_number"]),
                    message=c["message"],
                    severity=Severity(c.get("severity", "medium")),
                    issue_type=ReviewFocus(c.get("issue_type", "bugs")),
                    suggestion=c.get("suggestion"),
                )
                comments.append(comment)
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse comment: {e}")
                continue

        return ReviewResult(
            review_request_id=review_request_id,
            diff_revision=diff_revision,
            comments=comments,
            summary=data.get("summary", "Review completed"),
            has_critical_issues=data.get("has_critical_issues", False),
        )

    def format_comment_text(self, comment: ReviewComment) -> str:
        """Format a review comment for posting.

        Args:
            comment: The review comment.

        Returns:
            Formatted comment text.
        """
        severity_emoji = {
            Severity.LOW: "ℹ️",
            Severity.MEDIUM: "⚠️",
            Severity.HIGH: "🔴",
            Severity.CRITICAL: "🚨",
        }

        parts = [
            f"{severity_emoji.get(comment.severity, '•')} **{comment.severity.value.upper()}** ({comment.issue_type.value})",
            "",
            comment.message,
        ]

        if comment.suggestion:
            parts.extend(["", "**Suggestion:**", comment.suggestion])

        return "\n".join(parts)

    def format_review_summary(self, result: ReviewResult) -> str:
        """Format the overall review summary.

        Args:
            result: The review result.

        Returns:
            Formatted summary text.
        """
        if not result.comments:
            return (
                "✅ **AI Review Complete**\n\n"
                f"{result.summary}\n\n"
                "No issues found."
            )

        # Count issues by severity
        severity_counts = {}
        for c in result.comments:
            severity_counts[c.severity] = severity_counts.get(c.severity, 0) + 1

        parts = ["🤖 **AI Review Complete**", "", result.summary, "", "**Issue Summary:**"]

        for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
            count = severity_counts.get(severity, 0)
            if count:
                parts.append(f"- {severity.value.capitalize()}: {count}")

        if result.has_critical_issues:
            parts.extend([
                "",
                "⚠️ **Critical issues found. Please address before merging.**"
            ])

        return "\n".join(parts)


def extract_changed_files(diff: str) -> list[dict[str, Any]]:
    """Extract changed file information from a diff.

    Args:
        diff: Raw diff content.

    Returns:
        List of file change info dicts.
    """
    files = []
    current_file = None
    current_lines = []

    for line in diff.splitlines():
        # New file marker
        if line.startswith("diff --git"):
            if current_file:
                files.append({
                    "path": current_file,
                    "lines": current_lines,
                })
            # Extract file path - format is "diff --git a/path b/path"
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[3][2:]  # Remove "b/" prefix
            current_lines = []
        
        # Track added/modified line numbers
        elif line.startswith("@@"):
            # Parse hunk header like "@@ -10,5 +12,8 @@"
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                current_lines.append(start)

    # Don't forget the last file
    if current_file:
        files.append({
            "path": current_file,
            "lines": current_lines,
        })

    return files


def filter_diff_by_paths(diff: str, ignore_paths: list[str]) -> str:
    """Filter out ignored paths from a diff.

    Args:
        diff: Raw diff content.
        ignore_paths: List of path patterns to ignore.

    Returns:
        Filtered diff.
    """
    import fnmatch

    result_lines = []
    skip_current_file = False
    current_file_lines = []

    for line in diff.splitlines():
        if line.startswith("diff --git"):
            # Flush previous file if not skipped
            if not skip_current_file:
                result_lines.extend(current_file_lines)
            
            current_file_lines = [line]
            
            # Check if this file should be ignored
            parts = line.split()
            if len(parts) >= 4:
                file_path = parts[3][2:]  # Remove "b/" prefix
                skip_current_file = any(
                    fnmatch.fnmatch(file_path, pattern) or
                    fnmatch.fnmatch(file_path, f"**/{pattern}")
                    for pattern in ignore_paths
                )
            else:
                skip_current_file = False
        else:
            current_file_lines.append(line)

    # Don't forget the last file
    if not skip_current_file:
        result_lines.extend(current_file_lines)

    return "\n".join(result_lines)
