"""Provider factory: configuration decides which AI backend is used.

The application asks ``get_provider(cfg.llm)`` for a provider and passes it into services
(dependency injection). Optional providers are imported only when selected, so their SDKs
stay optional (``pip install -e ".[llm]"``) and the default install never needs them.
"""

from __future__ import annotations

from adm.ai.providers.base import LLMProvider, ProviderConfigError, ProviderError
from adm.ai.providers.mock import MockProvider
from adm.config import LLMSettings

__all__ = ["LLMProvider", "MockProvider", "ProviderConfigError", "ProviderError",
           "get_provider"]


def get_provider(settings: LLMSettings) -> LLMProvider:
    if settings.provider == "mock":
        return MockProvider(model=settings.model)
    if settings.provider == "openai_compatible":
        from adm.ai.providers.openai_compat import OpenAICompatibleProvider
        return OpenAICompatibleProvider(settings)
    if settings.provider == "anthropic":
        from adm.ai.providers.anthropic import AnthropicProvider
        return AnthropicProvider(settings)
    raise ProviderConfigError(f"Unknown AI provider '{settings.provider}'.")
