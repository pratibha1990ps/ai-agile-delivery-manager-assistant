"""T3: OpenAI-compatible and Anthropic providers, tested with fake SDKs (no network, no keys)."""

import sys
from types import SimpleNamespace

import pytest

from adm.ai.models import LLMRequest
from adm.ai.providers import LLMProvider, ProviderConfigError, ProviderError, get_provider
from adm.ai.providers.anthropic import FALLBACK_BETA, AnthropicProvider
from adm.ai.providers.openai_compat import LOCAL_PLACEHOLDER_KEY, OpenAICompatibleProvider
from adm.config import LLMSettings

REQ = LLMRequest(system="You are an assistant.", user="Facts: {...}", max_tokens=4000,
                 timeout_seconds=30)
SECRET = "sk-test-SECRET-123"


# --- fake SDKs ---------------------------------------------------------------------------

def fake_sdk(client_attr: str, response=None, error=None):
    """A stand-in module with the SDK's exception hierarchy and a recording client."""
    class APIConnectionError(Exception):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(Exception):
        def __init__(self, message="error", status_code=500):
            super().__init__(message)
            self.message, self.status_code = message, status_code

    errors = {name: type(name, (APIStatusError,), {}) for name in (
        "AuthenticationError", "PermissionDeniedError", "NotFoundError", "RateLimitError",
        "BadRequestError")}

    class Client:
        instances: list = []

        def __init__(self, **kwargs):
            self.kwargs, self.calls = kwargs, []
            Client.instances.append(self)
            endpoint = SimpleNamespace(create=self._create)
            self.chat = SimpleNamespace(completions=endpoint)        # OpenAI shape
            self.messages = endpoint                                 # Anthropic shape
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._beta_create))

        def _create(self, **params):
            self.calls.append(("create", params))
            if error:
                raise error(sdk)
            return response

        def _beta_create(self, **params):
            self.calls.append(("beta", params))
            if error:
                raise error(sdk)
            return response

    sdk = SimpleNamespace(APIConnectionError=APIConnectionError,
                          APITimeoutError=APITimeoutError, APIStatusError=APIStatusError,
                          **errors, **{client_attr: Client})
    return sdk


def openai_response(text='{"summary": "ok"}', finish="stop"):
    return SimpleNamespace(
        model="llama3.1",
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30))


def claude_response(text='{"summary": "ok"}', stop="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(
        model=model, stop_reason=stop,
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=900, output_tokens=200))


OLLAMA = LLMSettings(provider="openai_compatible", model="llama3.1",
                     base_url="http://localhost:11434/v1")
CLAUDE = LLMSettings(provider="anthropic", model="claude-opus-5-5")


# --- OpenAI-compatible -------------------------------------------------------------------

def test_openai_provider_satisfies_interface_and_returns_text():
    sdk = fake_sdk("OpenAI", openai_response())
    p = OpenAICompatibleProvider(OLLAMA, sdk=sdk)
    assert isinstance(p, LLMProvider)
    resp = p.complete(REQ)
    assert resp.text == '{"summary": "ok"}' and resp.provider == "openai_compatible"
    assert resp.usage == {"input_tokens": 120, "output_tokens": 30}


def test_openai_provider_sends_system_user_and_json_mode():
    sdk = fake_sdk("OpenAI", openai_response())
    OpenAICompatibleProvider(OLLAMA, sdk=sdk).complete(REQ)
    client = sdk.OpenAI.instances[-1]
    _, params = client.calls[0]
    assert [m["role"] for m in params["messages"]] == ["system", "user"]
    assert params["response_format"] == {"type": "json_object"}
    assert params["max_tokens"] == 4000
    assert client.kwargs["base_url"] == "http://localhost:11434/v1"


def test_local_server_needs_no_key_and_json_mode_can_be_disabled():
    sdk = fake_sdk("OpenAI", openai_response())
    p = OpenAICompatibleProvider(OLLAMA.model_copy(update={"json_mode": False}), sdk=sdk)
    p.complete(REQ)
    client = sdk.OpenAI.instances[-1]
    assert client.kwargs["api_key"] == LOCAL_PLACEHOLDER_KEY
    assert "response_format" not in client.calls[0][1]


def test_hosted_endpoint_without_key_or_base_url_is_a_config_error():
    with pytest.raises(ProviderConfigError, match="base_url"):
        OpenAICompatibleProvider(LLMSettings(provider="openai_compatible"),
                                 sdk=fake_sdk("OpenAI"))


def test_named_key_variable_must_exist(monkeypatch):
    monkeypatch.delenv("ADM_TEST_OPENAI_KEY", raising=False)
    settings = OLLAMA.model_copy(update={"api_key_env": "ADM_TEST_OPENAI_KEY"})
    with pytest.raises(ProviderConfigError, match="ADM_TEST_OPENAI_KEY"):
        OpenAICompatibleProvider(settings, sdk=fake_sdk("OpenAI"))


def test_truncated_openai_response_is_an_error():
    sdk = fake_sdk("OpenAI", openai_response(finish="length"))
    with pytest.raises(ProviderError, match="max_tokens"):
        OpenAICompatibleProvider(OLLAMA, sdk=sdk).complete(REQ)


@pytest.mark.parametrize("make_error,expected", [
    (lambda s: s.APITimeoutError("t"), "did not answer within 30s"),
    (lambda s: s.APIConnectionError("c"), "Could not connect"),
    (lambda s: s.AuthenticationError("a", 401), "credentials"),
    (lambda s: s.NotFoundError("n", 404), "llama3.1"),
    (lambda s: s.RateLimitError("r", 429), "rate limiting"),
    (lambda s: s.BadRequestError("b", 400), "json_mode"),
    (lambda s: s.APIStatusError("x", 503), "HTTP 503"),
])
def test_openai_errors_become_friendly_provider_errors(make_error, expected):
    p = OpenAICompatibleProvider(OLLAMA, sdk=fake_sdk("OpenAI", error=make_error))
    with pytest.raises(ProviderError, match=expected):
        p.complete(REQ)


# --- Anthropic ---------------------------------------------------------------------------

def test_anthropic_provider_joins_text_blocks_and_ignores_thinking():
    sdk = fake_sdk("Anthropic", claude_response())
    p = AnthropicProvider(CLAUDE, sdk=sdk)
    assert isinstance(p, LLMProvider)
    resp = p.complete(REQ)
    assert resp.text == '{"summary": "ok"}' and resp.model == "claude-opus-5-5"
    assert resp.usage == {"input_tokens": 900, "output_tokens": 200}


def test_anthropic_request_shape_has_no_temperature_and_opts_into_fallback():
    sdk = fake_sdk("Anthropic", claude_response())
    AnthropicProvider(CLAUDE, sdk=sdk).complete(REQ)
    kind, params = sdk.Anthropic.instances[-1].calls[0]
    assert kind == "beta" and params["betas"] == [FALLBACK_BETA]
    assert params["fallbacks"] == "default" and "temperature" not in params
    assert params["system"] == REQ.system and params["messages"][0]["role"] == "user"


def test_models_without_fallback_support_use_the_plain_endpoint():
    sdk = fake_sdk("Anthropic", claude_response(model="claude-haiku-5-5"))
    AnthropicProvider(CLAUDE.model_copy(update={"model": "claude-haiku-5-5"}),
                      sdk=sdk).complete(REQ)
    kind, params = sdk.Anthropic.instances[-1].calls[0]
    assert kind == "create" and "fallbacks" not in params


def test_served_model_is_recorded_when_a_fallback_answered():
    sdk = fake_sdk("Anthropic", claude_response(model="claude-opus-4-8"))
    assert AnthropicProvider(CLAUDE, sdk=sdk).complete(REQ).model == "claude-opus-4-8"


def test_sdk_resolves_credentials_when_no_key_variable_is_named():
    sdk = fake_sdk("Anthropic", claude_response())
    AnthropicProvider(CLAUDE, sdk=sdk)
    assert "api_key" not in sdk.Anthropic.instances[-1].kwargs


def test_named_key_is_passed_to_the_client_but_never_into_errors(monkeypatch):
    monkeypatch.setenv("ADM_TEST_CLAUDE_KEY", SECRET)
    settings = CLAUDE.model_copy(update={"api_key_env": "ADM_TEST_CLAUDE_KEY"})
    sdk = fake_sdk("Anthropic", error=lambda s: s.AuthenticationError("bad key", 401))
    p = AnthropicProvider(settings, sdk=sdk)
    assert sdk.Anthropic.instances[-1].kwargs["api_key"] == SECRET
    with pytest.raises(ProviderError) as info:
        p.complete(REQ)
    assert SECRET not in str(info.value)


@pytest.mark.parametrize("stop,expected", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_refusal_and_truncation_are_provider_errors(stop, expected):
    sdk = fake_sdk("Anthropic", claude_response(stop=stop))
    with pytest.raises(ProviderError, match=expected):
        AnthropicProvider(CLAUDE, sdk=sdk).complete(REQ)


@pytest.mark.parametrize("make_error,expected", [
    (lambda s: s.APITimeoutError("t"), "within 30s"),
    (lambda s: s.APIConnectionError("c"), "connect"),
    (lambda s: s.PermissionDeniedError("p", 403), "permission"),
    (lambda s: s.RateLimitError("r", 429), "rate limiting"),
    (lambda s: s.BadRequestError("max_tokens too large", 400), "max_tokens too large"),
    (lambda s: s.APIStatusError("x", 529), "HTTP 529"),
])
def test_anthropic_errors_become_friendly_provider_errors(make_error, expected):
    p = AnthropicProvider(CLAUDE, sdk=fake_sdk("Anthropic", error=make_error))
    with pytest.raises(ProviderError, match=expected):
        p.complete(REQ)


# --- factory and optional dependencies -----------------------------------------------------

@pytest.mark.parametrize("settings,module", [(OLLAMA, "openai"), (CLAUDE, "anthropic")])
def test_missing_optional_sdk_gives_install_hint(monkeypatch, settings, module):
    monkeypatch.setitem(sys.modules, module, None)   # makes `import <module>` fail
    with pytest.raises(ProviderConfigError, match=r"pip install -e"):
        get_provider(settings)
