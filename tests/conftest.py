"""Fixtures comunes: un gateway con proveedores ficticios y un doble de
`litellm.completion` que usa las respuestas simuladas de LiteLLM
(`mock_response`), de modo que los tests nunca llaman a un proveedor real
pero sí ejercitan el parseo real de respuestas de LiteLLM (y, en las
llamadas estructuradas, el de Instructor sobre un tool call simulado)."""

import hashlib
import json

import pytest
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.embeddings import EmbeddingError
from app.guardrails.base import FailurePolicy, GuardrailResult, ModerationUnavailableError
from app.semantic_cache.ports import CachedEntry, CacheUnavailableError
from app.services import llm_gateway  # antes que litellm: fija su configuración
from app.services.llm_gateway import litellm
from app.services.cache import TTLCache
from app.services.llm_gateway import LLMGateway, LLMResponse, ProviderConfig

_real_completion = litellm.completion

# Como en la app: structlog encaminado a logging (caplog captura los eventos).
configure_logging(get_settings())

PRIMARY = ProviderConfig(
    name="anthropic", model="anthropic/claude-haiku-4-5", api_key=SecretStr("sk-test-a")
)
SECONDARY = ProviderConfig(
    name="openai", model="openai/gpt-4o-mini", api_key=SecretStr("sk-test-o")
)


# Una estimación válida (los totales cuadran con las fases).
VALID_RESULT = {
    "summary": "Portal de reservas: 120 horas en 6 semanas, 6600 € a 55 €/h.",
    "total_hours": 120,
    "total_duration_weeks": 6,
    "total_cost_eur": 6600,
    "confidence_pct": 75,
    "phases": [
        {
            "name": "Diseño",
            "hours": 40,
            "duration_weeks": 2,
            "cost_eur": 2200,
            "confidence_pct": 80,
            "assumptions": ["el cliente aporta la guía de marca"],
        },
        {
            "name": "Desarrollo",
            "hours": 80,
            "duration_weeks": 4,
            "cost_eur": 4400,
            "confidence_pct": 70,
            "assumptions": [],
        },
    ],
}


class FakeCompletion:
    """Sustituye a litellm.completion. `failing` son los proveedores que
    fallan al conectar; `fail_mid_stream` los que fallan tras el primer
    fragmento. Las llamadas con `tools` (salida estructurada vía Instructor)
    responden con un tool call cuyos argumentos son, por orden, los de
    `structured` (el último se repite)."""

    def __init__(self, text: str = "Estimación: 40 horas") -> None:
        self.text = text
        self.structured: list[dict | str] = [VALID_RESULT]
        self.structured_calls = 0
        self.last_kwargs: dict | None = None
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
        self.last_kwargs = kwargs
        if provider in self.failing:
            raise self.error_cls(message="proveedor caído", llm_provider=provider, model=model)
        kwargs.pop("api_key")
        response = _real_completion(**kwargs, api_key="unused", **self._mock(kwargs))
        if kwargs.get("stream") and provider in self.fail_mid_stream:
            return self._broken_stream(response, provider, model)
        return response

    def _mock(self, kwargs: dict) -> dict:
        if "tools" not in kwargs:
            return {"mock_response": self.text}
        output = self.structured[min(self.structured_calls, len(self.structured) - 1)]
        self.structured_calls += 1
        arguments = output if isinstance(output, str) else json.dumps(output)
        tool_call = {
            "id": f"call_{self.structured_calls}",
            "type": "function",
            "function": {"name": kwargs["tools"][0]["function"]["name"], "arguments": arguments},
        }
        return {"mock_tool_calls": [tool_call]}

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


def make_gateway(max_retries: int = 0, **kwargs) -> LLMGateway:
    kwargs.setdefault("cache", TTLCache[LLMResponse](ttl_seconds=60, max_entries=10))
    return LLMGateway(
        [PRIMARY, SECONDARY],
        timeout_seconds=5,
        max_retries=max_retries,
        max_tokens=256,
        retry_backoff_seconds=0,
        validation_retries=2,
        **kwargs,
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


# ---------------------------------------------------------------- guardrails


class FakeModeration:
    """Sustituye al cliente de la Moderation API (sin red). `flagged` simula
    contenido marcado; `unavailable` simula un timeout de la API."""

    def __init__(self) -> None:
        self.flagged = False
        self.unavailable = False
        self.calls: list[str] = []

    def check(self, text: str) -> GuardrailResult:
        self.calls.append(text)
        if self.unavailable:
            raise ModerationUnavailableError("APITimeoutError")
        score = 0.97 if self.flagged else 0.01
        return GuardrailResult(
            name="moderation",
            triggered=self.flagged,
            policy=FailurePolicy.EXCEPTION,
            score=score,
            internal_detail="violence" if self.flagged else None,
            scores={"violence": score, "hate": 0.0},
        )


@pytest.fixture
def fake_moderation() -> FakeModeration:
    return FakeModeration()


def make_settings(**overrides) -> Settings:
    """Settings herméticos: ignoran el .env local del desarrollador."""
    base = {"_env_file": None, "openai_api_key": "sk-test-o", "semantic_cache_mode": "off"}
    return Settings(**(base | overrides))


def guardrail_events(caplog, name: str = "guardrail.evaluated") -> list[dict]:
    """Eventos structlog capturados por caplog (record.msg es el dict)."""
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == name
    ]


# ------------------------------------------------------------ caché semántico


class FakeEmbedder:
    """Embeddings deterministas sin red. Por defecto cada texto distinto da
    un vector distinto; `vectors` permite fijar el vector de un texto (p. ej.
    para que una reformulación caiga cerca). `failing` simula un timeout."""

    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}
        self.failing = False
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.failing:
            raise EmbeddingError("APITimeoutError")
        if text in self.vectors:
            return self.vectors[text]
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:8]]


class FakeSemanticCache:
    """Caché en memoria con la misma semántica de bucket que el real; hit
    solo con el mismo vector exacto (la similitud real se prueba en los
    tests de integración con Redis Stack). `failing` simula Redis caído."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, tuple[float, ...]], str] = {}
        self.failing = False
        self.lookups: list[str] = []
        self.stores: list[str] = []

    def lookup(self, *, bucket: str, vector: list[float]) -> CachedEntry | None:
        self.lookups.append(bucket)
        if self.failing:
            raise CacheUnavailableError("TimeoutError")
        response = self.entries.get((bucket, tuple(vector)))
        return CachedEntry(response=response, distance=0.01) if response is not None else None

    def store(self, *, bucket: str, vector: list[float], response: str) -> None:
        self.stores.append(bucket)
        if self.failing:
            raise CacheUnavailableError("TimeoutError")
        self.entries[(bucket, tuple(vector))] = response


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_cache() -> FakeSemanticCache:
    return FakeSemanticCache()
