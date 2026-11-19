"""The provider interface every AI backend implements.

Business logic depends only on ``LLMProvider``: it sends an ``LLMRequest`` and receives an
``LLMResponse``. Which backend answers (mock, a local Ollama server, Anthropic, ...) is a
configuration choice, so swapping providers never touches the RAID Analyst or validator.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from adm.ai.models import LLMRequest, LLMResponse


class ProviderError(RuntimeError):
    """A provider could not produce a response (network, timeout, auth, bad config).

    Messages are written for the user and never include API keys.
    """


class ProviderConfigError(ProviderError):
    """The provider is misconfigured or its optional SDK is not installed."""


@runtime_checkable
class LLMProvider(Protocol):
    name: str    # provider family, e.g. "mock", "openai_compatible", "anthropic"
    model: str   # model identifier recorded in the audit log

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the model's raw text. Never raise anything except ``ProviderError``."""
        ...
