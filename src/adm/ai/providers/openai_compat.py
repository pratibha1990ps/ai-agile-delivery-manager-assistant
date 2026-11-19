"""Provider for any OpenAI-compatible chat endpoint.

One class covers free local model servers (Ollama, LM Studio, vLLM) and hosted services that
speak the same protocol: only ``base_url``, ``model`` and the key's environment variable
change. The ``openai`` SDK is optional (``pip install -e ".[llm]"``) and imported lazily.
"""

from __future__ import annotations

import time
from types import ModuleType
from typing import Any

from adm.ai.models import LLMRequest, LLMResponse
from adm.ai.providers.base import ProviderConfigError, ProviderError
from adm.config import LLMSettings

LOCAL_PLACEHOLDER_KEY = "not-needed"   # local servers ignore the key, but the SDK requires one


def _import_sdk() -> ModuleType:
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ProviderConfigError(
            "The 'openai' package is not installed. Run: pip install -e \".[llm]\"") from exc
    return openai


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, settings: LLMSettings, sdk: ModuleType | Any | None = None) -> None:
        self.settings = settings
        self.model = settings.model
        self._sdk = sdk or _import_sdk()

        key = settings.api_key()
        if settings.api_key_env and not key:
            raise ProviderConfigError(
                f"Environment variable {settings.api_key_env} is not set.")
        if not key:
            if not settings.base_url:
                raise ProviderConfigError(
                    "Set base_url for a local server, or api_key_env for a hosted service.")
            key = LOCAL_PLACEHOLDER_KEY
        self._client = self._sdk.OpenAI(base_url=settings.base_url, api_key=key,
                                        timeout=settings.timeout_seconds, max_retries=1)

    def complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": request.system},
                         {"role": "user", "content": request.user}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if self.settings.json_mode:
            params["response_format"] = {"type": "json_object"}

        start = time.perf_counter()
        sdk = self._sdk
        where = self.settings.base_url or "the configured endpoint"
        try:
            resp = self._client.chat.completions.create(**params)
        except sdk.APITimeoutError as exc:
            raise ProviderError(
                f"The AI provider did not answer within {request.timeout_seconds:.0f}s.") from exc
        except sdk.AuthenticationError as exc:
            raise ProviderError("The AI provider rejected the credentials.") from exc
        except sdk.RateLimitError as exc:
            raise ProviderError("The AI provider is rate limiting requests; try later.") from exc
        except sdk.NotFoundError as exc:
            raise ProviderError(f"Model '{self.model}' was not found at {where}.") from exc
        except sdk.BadRequestError as exc:
            raise ProviderError(
                "The AI provider rejected the request (if it does not support JSON mode, set "
                "json_mode: false).") from exc
        except sdk.APIStatusError as exc:
            raise ProviderError(
                f"The AI provider returned an error (HTTP {exc.status_code}).") from exc
        except sdk.APIConnectionError as exc:
            raise ProviderError(f"Could not connect to the AI provider at {where}.") from exc

        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise ProviderError("The AI response was cut off at max_tokens; raise max_tokens.")
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=choice.message.content or "", provider=self.name,
            model=getattr(resp, "model", None) or self.model,
            latency_ms=int((time.perf_counter() - start) * 1000),
            usage={"input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                   "output_tokens": getattr(usage, "completion_tokens", 0) or 0} if usage else {},
        )
