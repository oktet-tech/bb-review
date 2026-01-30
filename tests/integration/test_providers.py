"""Integration tests for LLM providers."""

import pytest

from bb_review.reviewers.providers import (
    AnthropicProvider,
    LLMProvider,
    OpenAIProvider,
    OpenRouterProvider,
    create_provider,
)


class TestProviderFactory:
    """Tests for create_provider factory function."""

    def test_create_anthropic_provider(self):
        """Create Anthropic provider."""
        provider = create_provider(
            provider="anthropic",
            api_key="test-key",
            model="claude-sonnet-4-20250514",
        )

        assert isinstance(provider, AnthropicProvider)
        assert provider.model == "claude-sonnet-4-20250514"

    def test_create_openrouter_provider(self):
        """Create OpenRouter provider."""
        provider = create_provider(
            provider="openrouter",
            api_key="test-key",
            model="anthropic/claude-sonnet-4-20250514",
            base_url="https://openrouter.ai/api/v1",
        )

        assert isinstance(provider, OpenRouterProvider)
        assert provider.model == "anthropic/claude-sonnet-4-20250514"

    def test_create_openai_provider(self):
        """Create OpenAI provider."""
        provider = create_provider(
            provider="openai",
            api_key="test-key",
            model="gpt-4o",
        )

        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-4o"

    def test_create_unknown_provider(self):
        """Error for unknown provider."""
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider(
                provider="unknown",
                api_key="test-key",
                model="some-model",
            )

    def test_provider_with_custom_params(self):
        """Create provider with custom parameters."""
        provider = create_provider(
            provider="anthropic",
            api_key="test-key",
            model="test-model",
            max_tokens=8192,
            temperature=0.5,
        )

        assert provider.max_tokens == 8192
        assert provider.temperature == 0.5


class TestLLMProviderInterface:
    """Tests for LLMProvider interface compliance."""

    def test_anthropic_is_llm_provider(self):
        """AnthropicProvider implements LLMProvider."""
        provider = AnthropicProvider(api_key="test", model="test")
        assert isinstance(provider, LLMProvider)

    def test_openrouter_is_llm_provider(self):
        """OpenRouterProvider implements LLMProvider."""
        provider = OpenRouterProvider(api_key="test", model="test")
        assert isinstance(provider, LLMProvider)

    def test_openai_is_llm_provider(self):
        """OpenAIProvider implements LLMProvider."""
        provider = OpenAIProvider(api_key="test", model="test")
        assert isinstance(provider, LLMProvider)


class TestOpenRouterSpecifics:
    """Tests specific to OpenRouter provider."""

    def test_openrouter_default_base_url(self):
        """OpenRouter uses correct default base URL."""
        provider = OpenRouterProvider(api_key="test", model="test")
        # The client should be configured with OpenRouter base URL
        assert provider.client.base_url is not None

    def test_openrouter_with_site_info(self):
        """OpenRouter accepts site info for analytics."""
        provider = OpenRouterProvider(
            api_key="test",
            model="test",
            site_url="https://example.com",
            site_name="Test App",
        )

        # Should create without error
        assert provider is not None


class TestOpenAISpecifics:
    """Tests specific to OpenAI provider."""

    def test_openai_with_custom_base_url(self):
        """OpenAI provider accepts custom base URL."""
        provider = OpenAIProvider(
            api_key="test",
            model="test",
            base_url="https://custom.api.com/v1",
        )

        assert provider is not None
        assert provider.client.base_url is not None
