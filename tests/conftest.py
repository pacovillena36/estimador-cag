"""Fixtures comunes: un gateway con proveedores ficticios y un doble de
`litellm.completion` que usa las respuestas simuladas de LiteLLM
(`mock_response`), de modo que los tests nunca llaman a un proveedor real
pero sí ejercitan el parseo real de respuestas de LiteLLM."""

import pytest
from pydantic import SecretStr

from app.services import llm_gateway  # antes que litellm: fija su configuración
from app.services.llm_gateway import litellm
from app.services.cache import TTLCache
from app.services.llm_gateway import LLMGateway, LLMResponse, ProviderConfig

_real_completion = litellm.completion

PRIMARY = ProviderConfig(
    name="anthropic", model="anthropic/claude-haiku-4-5", api_key=SecretStr("sk-test-a")
)
SECONDARY = ProviderConfig(
    name="openai", model="openai/gpt-4o-mini", api_key=SecretStr("sk-test-o")
)


class FakeCompletion:
    """Sustituye a litellm.completion. `failing` son los proveedores que
    fallan al conectar; `fail_mid_stream` los que fallan tras el primer
    fragmento."""

    def __init__(self, text: str = "Estimación: 40 horas") -> None:
        self.text = text
        self.failing: set[str] = set()
        self.error_cls = litellm.exceptions.ServiceUnavailableError
        self.fail_mid_stream: set[str] = set()
        self.calls: list[str] = []
        self.last_messages: list[dict] | None = None

    def __call__(self, **kwargs):
        model = kwargs["model"]
        provider = model.split("/", 1)[0]
        self.calls.append(provider)
        self.last_messages = kwargs["messages"]
        if provider in self.failing:
            raise self.error_cls(message="proveedor caído", llm_provider=provider, model=model)
        kwargs.pop("api_key")
        response = _real_completion(**kwargs, api_key="unused", mock_response=self.text)
        if kwargs.get("stream") and provider in self.fail_mid_stream:
            return self._broken_stream(response, provider, model)
        return response

    @staticmethod
    def _broken_stream(stream, provider, model):
        yield next(iter(stream))
        raise litellm.exceptions.APIConnectionError(
            message="conexión perdida", llm_provider=provider, model=model
        )


@pytest.fixture
def fake_completion(monkeypatch) -> FakeCompletion:
    fake = FakeCompletion()
    monkeypatch.setattr(llm_gateway.litellm, "completion", fake)
    return fake


def make_gateway(max_retries: int = 0) -> LLMGateway:
    return LLMGateway(
        [PRIMARY, SECONDARY],
        timeout_seconds=5,
        max_retries=max_retries,
        max_tokens=256,
        cache=TTLCache[LLMResponse](ttl_seconds=60, max_entries=10),
        retry_backoff_seconds=0,
    )


@pytest.fixture
def gateway() -> LLMGateway:
    return make_gateway()


@pytest.fixture
def logs(monkeypatch) -> list[tuple[str, str, dict]]:
    """Captura los eventos que emite el wrapper: (nivel, evento, campos)."""
    captured: list[tuple[str, str, dict]] = []

    class Recorder:
        def __getattr__(self, level):
            return lambda event, **fields: captured.append((level, event, fields))

    monkeypatch.setattr(llm_gateway, "log", Recorder())
    return captured
