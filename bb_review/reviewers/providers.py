"""LLM providers for code review."""

from abc import ABC, abstractmethod
import logging

import anthropic
import openai


logger = logging.getLogger(__name__)


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
        site_url: str | None = None,
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
        message = response.choices[0].message
        content = message.content or ""

        # Some models (like deepseek-r1) return reasoning in a separate field
        # and put the actual response in reasoning_content
        reasoning = getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None)
        if reasoning:
            logger.debug(f"Model reasoning ({len(reasoning)} chars)")
            # If content is empty but we have reasoning, use reasoning as content
            # This handles deepseek-r1 which puts JSON in reasoning_content
            if not content:
                logger.info("Using reasoning_content as response (deepseek-r1 style)")
                content = reasoning

        return content


class OpenAIProvider(LLMProvider):
    """OpenAI provider."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.2,
        base_url: str | None = None,
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
    base_url: str | None = None,
    site_url: str | None = None,
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
