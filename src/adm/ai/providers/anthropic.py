"""Provider for Anthropic's Claude models via the official ``anthropic`` SDK (optional extra).

Notes that follow the current Claude API:

* Credentials: if ``api_key_env`` is set, that variable must exist. If it is not set, the SDK
  resolves credentials itself (``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN`` or a CLI
  profile). The key is never stored, logged or put into a prompt.
* No ``temperature``: current API versions reject sampling parameters on every model.
* Current models think before answering; thinking blocks come back empty and are ignored here,
  and only ``text`` blocks form the answer. Thinking counts towards ``max_tokens``, so keep it
  generous.
* Refusals: for models that support it, the request opts into server-side fallback, so a policy
  decline is re-run on a fallback model inside the same call. A final refusal becomes a
  ``ProviderError`` instead of an empty answer.
"""

from __future__ import annotations

import time
from types import ModuleType
from typing import Any

from adm.ai.models import LLMRequest, LLMResponse
from adm.ai.providers.base import ProviderConfigError, ProviderError
from adm.config import LLMSettings

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side ``fallbacks`` parameter.
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5",
                   "claude-sonnet-5-5"}


def _import_sdk() -> ModuleType:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ProviderConfigError(
            "The 'anthropic' package is not installed. Run: pip install -e \".[llm]\"") from exc
    return anthropic


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: LLMSettings, sdk: ModuleType | Any | None = None) -> None:
        self.settings = settings
        self.model = settings.model
        self._sdk = sdk or _import_sdk()

        kwargs: dict[str, Any] = {"timeout": settings.timeout_seconds, "max_retries": 2}
        if settings.api_key_env:
            key = settings.api_key()
            if not key:
                raise ProviderConfigError(
                    f"Environment variable {settings.api_key_env} is not set.")
            kwargs["api_key"] = key
        self._client = self._sdk.Anthropic(**kwargs)

    def complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        sdk = self._sdk
        start = time.perf_counter()
        try:
            if self.model in FALLBACK_MODELS:
                resp = self._client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = self._client.messages.create(**params)
        except sdk.APITimeoutError as exc:
            raise ProviderError(
                f"Claude did not answer within {request.timeout_seconds:.0f}s.") from exc
        except sdk.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the credentials.") from exc
        except sdk.PermissionDeniedError as exc:
            raise ProviderError("The Anthropic credentials lack permission for this model.") \
                from exc
        except sdk.NotFoundError as exc:
            raise ProviderError(f"Model '{self.model}' was not found.") from exc
        except sdk.RateLimitError as exc:
            raise ProviderError("Anthropic is rate limiting requests; try again later.") from exc
        except sdk.BadRequestError as exc:
            raise ProviderError(f"Anthropic rejected the request: {exc.message}") from exc
        except sdk.APIStatusError as exc:
            raise ProviderError(f"Anthropic returned an error (HTTP {exc.status_code}).") \
                from exc
        except sdk.APIConnectionError as exc:
            raise ProviderError("Could not connect to the Anthropic API.") from exc

        if resp.stop_reason == "refusal":
            raise ProviderError("Claude declined this request (refusal).")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("The Claude response was cut off at max_tokens; raise it.")
        text = "".join(block.text for block in resp.content if block.type == "text")
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text, provider=self.name,
            model=getattr(resp, "model", None) or self.model,   # the model that actually served
            latency_ms=int((time.perf_counter() - start) * 1000),
            usage={"input_tokens": getattr(usage, "input_tokens", 0) or 0,
                   "output_tokens": getattr(usage, "output_tokens", 0) or 0} if usage else {},
        )
