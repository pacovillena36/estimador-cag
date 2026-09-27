"""Wrapper de proveedores LLM construido sobre LiteLLM.

Responsabilidades (y lo único que sabe de proveedores la aplicación):

- Abstracción: LiteLLM expone una única API (formato OpenAI) para OpenAI y
  Anthropic; el wrapper devuelve siempre un `LLMResponse` normalizado.
- Reintentos: los errores transitorios (timeout, rate limit, 5xx, conexión)
  se reintentan sobre el mismo proveedor con backoff exponencial; los de
  autenticación o petición inválida no, porque fallarían igual.
- Fallback: agotados los reintentos, rota al siguiente proveedor. En
  streaming solo se rota antes de emitir el primer fragmento; una vez
  enviado texto al cliente no se puede cambiar de proveedor.
- Caché exact-match: misma petición (prompt + parámetros) -> misma respuesta,
  sin volver a llamar al proveedor.
- Logging estructurado de cada llamada (ver "Eventos de log" más abajo).
  Nunca se registran transcripciones, respuestas ni claves.

Los consumidores (endpoints) no saben ni les importa qué proveedor respondió.

Eventos de log (uno de inicio y uno de cierre por llamada, más los fallos):

- llm.call_started    requested_model, target_provider,
                      input_tokens_estimated, cache_hit
- llm.provider_failed provider, model, error_category (timeout, rate_limit,
                      auth, server_error, connection, bad_request, unknown),
                      error_type, status_code, retry, will_retry,
                      will_fallback, latency_ms
- llm.fallback        from_provider, to_provider, to_model
- llm.call_completed  provider, model, input_tokens, output_tokens,
                      latency_ms, cost_usd, finish_reason, cache_hit,
                      fallback_used, fallback_provider, attempts
- llm.call_failed     todos los proveedores fallaron (attempts, latency_ms)
"""

import hashlib
import json
import os
import time
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, NoReturn, TypeVar

# Usa el mapa de costes de modelos que trae el paquete en lugar de
# descargarlo de GitHub al importar LiteLLM (evita una petición saliente
# no controlada en cada arranque). Debe fijarse antes de importar litellm.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402
import openai  # noqa: E402
import structlog  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.services.cache import TTLCache  # noqa: E402

# Sin telemetría hacia terceros ni banners de depuración en los errores.
litellm.telemetry = False
litellm.suppress_debug_info = True

log = structlog.get_logger(__name__)

# Todas las excepciones que LiteLLM mapea desde los proveedores (timeouts,
# rate limits, auth, 5xx, conexión...) heredan de openai.APIError.
ProviderError = openai.APIError

# Categorías de error que merece la pena reintentar sobre el mismo proveedor.
RETRYABLE_ERRORS = frozenset({"timeout", "rate_limit", "server_error", "connection"})

T = TypeVar("T")


class LLMGatewayError(Exception):
    """Error base del wrapper."""


class NoProvidersConfiguredError(LLMGatewayError):
    """Ningún proveedor tiene API key configurada."""


class AllProvidersFailedError(LLMGatewayError):
    """Todos los proveedores de la cadena de fallback han fallado."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str  # identificador LiteLLM, p. ej. "anthropic/claude-haiku-4-5"
    api_key: SecretStr
    extra_headers: dict[str, str] | None = None

    def __repr__(self) -> str:  # nunca exponer la clave
        return f"ProviderConfig(name={self.name!r}, model={self.model!r})"


@dataclass(frozen=True)
class LLMResponse:
    """Respuesta normalizada, independiente del proveedor que la generó."""

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    elapsed_seconds: float | None = None  # latencia total de la llamada
    cost_usd: float | None = None
    finish_reason: str | None = None  # "stop" = completa, "length" = truncada
    cached: bool = False


class LLMStream:
    """Iterador de fragmentos de texto. `response` queda relleno (tokens,
    modelo, latencia, coste) una vez consumido el iterador por completo."""

    def __init__(self, chunks: Generator[str, None, LLMResponse]) -> None:
        self._chunks = chunks
        self.response: LLMResponse | None = None

    def __iter__(self) -> Iterator[str]:
        self.response = yield from self._chunks


@dataclass
class _CallContext:
    """Estado de una llamada al wrapper, compartido entre intentos."""

    primary: ProviderConfig
    start: float
    attempts: int = 0

    def latency_ms(self) -> float:
        return round((time.perf_counter() - self.start) * 1000, 1)


def classify_error(exc: Exception) -> str:
    """Traduce las excepciones de LiteLLM a una categoría estable."""
    exceptions = litellm.exceptions
    # El orden importa: Timeout hereda de APIConnectionError.
    if isinstance(exc, exceptions.Timeout):
        return "timeout"
    if isinstance(exc, exceptions.RateLimitError):
        return "rate_limit"
    if isinstance(exc, (exceptions.AuthenticationError, exceptions.PermissionDeniedError)):
        return "auth"
    if isinstance(exc, exceptions.APIConnectionError):
        return "connection"
    if isinstance(exc, (exceptions.BadRequestError, exceptions.NotFoundError)):
        return "bad_request"
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code >= 500:
        return "server_error"
    if isinstance(exc, (exceptions.InternalServerError, exceptions.ServiceUnavailableError)):
        return "server_error"
    return "unknown"


def build_providers(settings: Settings) -> list[ProviderConfig]:
    """Orden de la cadena de fallback: primero el proveedor preferido
    (LLM_PROVIDER) y después el resto, omitiendo los que no tienen clave."""
    available: dict[str, ProviderConfig] = {}

    if settings.openai_api_key and settings.openai_api_key.get_secret_value():
        available["openai"] = ProviderConfig(
            name="openai",
            model=f"openai/{settings.openai_model}",
            api_key=settings.openai_api_key,
        )
    if settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value():
        headers = (
            {"anthropic-workspace-id": settings.anthropic_workspace_id}
            if settings.anthropic_workspace_id
            else None
        )
        available["anthropic"] = ProviderConfig(
            name="anthropic",
            model=f"anthropic/{settings.anthropic_model}",
            api_key=settings.anthropic_api_key,
            extra_headers=headers,
        )

    order = [settings.llm_provider] + [
        name for name in available if name != settings.llm_provider
    ]
    if not settings.llm_fallback_enabled:
        order = order[:1]
    return [available[name] for name in order if name in available]


class LLMGateway:
    def __init__(
        self,
        providers: list[ProviderConfig],
        *,
        timeout_seconds: float,
        max_retries: int,
        max_tokens: int,
        cache: TTLCache[LLMResponse] | None = None,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        if not providers:
            raise NoProvidersConfiguredError(
                "No hay ningún proveedor LLM con API key configurada "
                "(OPENAI_API_KEY / ANTHROPIC_API_KEY)."
            )
        self._providers = providers
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._cache = cache
        self._retry_backoff = retry_backoff_seconds

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    # ------------------------------------------------------------------ API

    def complete(self, system_prompt: str, user_message: str) -> LLMResponse:
        messages = self._messages(system_prompt, user_message)
        cache_key = self._cache_key(messages)
        ctx = _CallContext(primary=self._providers[0], start=time.perf_counter())

        cached = self._cache_get(cache_key)
        self._log_started(ctx, messages, cache_hit=cached is not None)
        if cached:
            return self._serve_cached(ctx, cached)

        for index, provider in enumerate(self._providers):
            self._log_fallback(index, provider)
            raw = self._call_with_retries(
                ctx,
                provider,
                has_fallback=index < len(self._providers) - 1,
                call=lambda p=provider: litellm.completion(**self._request_kwargs(p, messages)),
            )
            if raw is None:
                continue

            choice = raw.choices[0]
            response = self._build_response(
                ctx,
                provider,
                text=choice.message.content or "",
                model=raw.model or provider.model,
                input_tokens=getattr(raw.usage, "prompt_tokens", None),
                output_tokens=getattr(raw.usage, "completion_tokens", None),
                finish_reason=choice.finish_reason,
            )
            self._log_completed(ctx, response)
            self._cache_set(cache_key, response)
            return response

        self._raise_all_failed(ctx)

    def stream(self, system_prompt: str, user_message: str) -> LLMStream:
        """Abre el stream de forma inmediata (no perezosa): conecta con el
        proveedor y lee el primer fragmento antes de devolver, de modo que
        los fallos de conexión/autenticación/rate limit se detectan aquí y
        se puede reintentar o rotar de proveedor, o elevar
        AllProvidersFailedError antes de que el endpoint haya empezado a
        responder al cliente."""
        messages = self._messages(system_prompt, user_message)
        cache_key = self._cache_key(messages)
        ctx = _CallContext(primary=self._providers[0], start=time.perf_counter())

        cached = self._cache_get(cache_key)
        self._log_started(ctx, messages, cache_hit=cached is not None)
        if cached:
            return LLMStream(self._replay(self._serve_cached(ctx, cached)))

        def open_stream(provider: ProviderConfig) -> tuple[Iterator, Any]:
            raw_iter = iter(
                litellm.completion(
                    **self._request_kwargs(provider, messages),
                    stream=True,
                    stream_options={"include_usage": True},
                )
            )
            return raw_iter, next(raw_iter, None)

        for index, provider in enumerate(self._providers):
            self._log_fallback(index, provider)
            opened = self._call_with_retries(
                ctx,
                provider,
                has_fallback=index < len(self._providers) - 1,
                call=lambda p=provider: open_stream(p),
            )
            if opened is None:
                continue

            raw_iter, first_chunk = opened
            return LLMStream(
                self._consume_stream(ctx, provider, raw_iter, first_chunk, cache_key)
            )

        self._raise_all_failed(ctx)

    # ------------------------------------------------------------ internals

    def _call_with_retries(
        self,
        ctx: _CallContext,
        provider: ProviderConfig,
        *,
        has_fallback: bool,
        call: Callable[[], T],
    ) -> T | None:
        """Ejecuta `call` contra un proveedor, reintentando los errores
        transitorios. Devuelve None si el proveedor queda descartado."""
        for retry in range(self._max_retries + 1):
            ctx.attempts += 1
            attempt_start = time.perf_counter()
            try:
                return call()
            except ProviderError as exc:
                category = classify_error(exc)
                will_retry = category in RETRYABLE_ERRORS and retry < self._max_retries
                self._log_failure(
                    provider,
                    exc,
                    category=category,
                    retry=retry,
                    will_retry=will_retry,
                    will_fallback=not will_retry and has_fallback,
                    latency_ms=round((time.perf_counter() - attempt_start) * 1000, 1),
                )
                if not will_retry:
                    return None
                time.sleep(self._retry_backoff * 2**retry)
        return None

    def _consume_stream(
        self,
        ctx: _CallContext,
        provider: ProviderConfig,
        raw_iter: Iterator,
        first_chunk: Any,
        cache_key: str,
    ) -> Generator[str, None, LLMResponse]:
        parts: list[str] = []
        model = provider.model
        input_tokens = output_tokens = None
        finish_reason = None

        def chunks():
            if first_chunk is not None:
                yield first_chunk
            yield from raw_iter

        try:
            for chunk in chunks():
                model = getattr(chunk, "model", None) or model
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    input_tokens = usage.prompt_tokens
                    output_tokens = usage.completion_tokens
                if chunk.choices:
                    choice = chunk.choices[0]
                    finish_reason = choice.finish_reason or finish_reason
                    if choice.delta.content:
                        parts.append(choice.delta.content)
                        yield choice.delta.content
        except ProviderError as exc:
            # Ya se ha enviado texto al cliente: no se puede rotar de
            # proveedor sin duplicar/mezclar la respuesta.
            log.error(
                "llm.stream_interrupted",
                provider=provider.name,
                model=provider.model,
                error_category=classify_error(exc),
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
                chars_sent=sum(len(p) for p in parts),
                latency_ms=ctx.latency_ms(),
            )
            raise

        response = self._build_response(
            ctx,
            provider,
            text="".join(parts),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )
        self._log_completed(ctx, response)
        self._cache_set(cache_key, response)
        return response

    @staticmethod
    def _replay(cached: LLMResponse) -> Generator[str, None, LLMResponse]:
        yield cached.text
        return cached

    def _build_response(
        self,
        ctx: _CallContext,
        provider: ProviderConfig,
        *,
        text: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        finish_reason: str | None,
    ) -> LLMResponse:
        return LLMResponse(
            text=text,
            provider=provider.name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=time.perf_counter() - ctx.start,
            cost_usd=self._estimate_cost(provider.model, input_tokens, output_tokens),
            finish_reason=finish_reason,
        )

    def _serve_cached(self, ctx: _CallContext, cached: LLMResponse) -> LLMResponse:
        # Una respuesta cacheada no llama al proveedor: coste 0.
        response = replace(
            cached,
            cached=True,
            cost_usd=0.0,
            elapsed_seconds=time.perf_counter() - ctx.start,
        )
        self._log_completed(ctx, response)
        return response

    def _request_kwargs(self, provider: ProviderConfig, messages: list[dict]) -> dict:
        kwargs = {
            "model": provider.model,
            "messages": messages,
            "api_key": provider.api_key.get_secret_value(),
            "max_tokens": self._max_tokens,
            "timeout": self._timeout,
            # Los reintentos los gestiona el wrapper (_call_with_retries)
            # para poder registrarlos y reintentar solo errores transitorios.
            "num_retries": 0,
        }
        if provider.extra_headers:
            kwargs["extra_headers"] = provider.extra_headers
        return kwargs

    @staticmethod
    def _messages(system_prompt: str, user_message: str) -> list[dict]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def _cache_key(self, messages: list[dict]) -> str:
        # Exact-match sobre todo lo que determina la respuesta: mensajes,
        # parámetros de generación y cadena de modelos. Se guarda solo el
        # hash SHA-256, nunca el texto de la transcripción como clave.
        payload = json.dumps(
            {
                "messages": messages,
                "max_tokens": self._max_tokens,
                "models": [p.model for p in self._providers],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> LLMResponse | None:
        return self._cache.get(key) if self._cache is not None else None

    def _cache_set(self, key: str, response: LLMResponse) -> None:
        # Solo se cachean respuestas completas y no vacías.
        if self._cache is not None and response.text.strip():
            self._cache.set(key, response)

    @staticmethod
    def _estimate_input_tokens(model: str, messages: list[dict]) -> int | None:
        try:
            return litellm.token_counter(model=model, messages=messages)
        except Exception:  # la estimación nunca debe romper la llamada
            return None

    @staticmethod
    def _estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
        if input_tokens is None or output_tokens is None:
            return None
        try:
            prompt_cost, completion_cost = litellm.cost_per_token(
                model=model, prompt_tokens=input_tokens, completion_tokens=output_tokens
            )
        except Exception:  # modelo sin precio en el mapa de LiteLLM
            return None
        return round(prompt_cost + completion_cost, 6)

    # --------------------------------------------------------------- logging

    def _log_started(self, ctx: _CallContext, messages: list[dict], *, cache_hit: bool) -> None:
        log.info(
            "llm.call_started",
            requested_model=ctx.primary.model,
            target_provider=ctx.primary.name,
            input_tokens_estimated=self._estimate_input_tokens(ctx.primary.model, messages),
            cache_hit=cache_hit,
        )

    def _log_fallback(self, index: int, provider: ProviderConfig) -> None:
        if index == 0:
            return
        log.info(
            "llm.fallback",
            from_provider=self._providers[index - 1].name,
            to_provider=provider.name,
            to_model=provider.model,
        )

    @staticmethod
    def _log_failure(
        provider: ProviderConfig,
        exc: Exception,
        *,
        category: str,
        retry: int,
        will_retry: bool,
        will_fallback: bool,
        latency_ms: float,
    ) -> None:
        log.warning(
            "llm.provider_failed",
            provider=provider.name,
            model=provider.model,
            error_category=category,
            error_type=type(exc).__name__,
            status_code=getattr(exc, "status_code", None),
            retry=retry,
            will_retry=will_retry,
            will_fallback=will_fallback,
            latency_ms=latency_ms,
        )
        # El mensaje del proveedor solo en DEBUG: puede ser largo y, en
        # algunos errores, reflejar parte de la petición.
        log.debug("llm.provider_error_detail", provider=provider.name, error=str(exc)[:500])

    @staticmethod
    def _log_completed(ctx: _CallContext, response: LLMResponse) -> None:
        fallback_used = not response.cached and response.provider != ctx.primary.name
        log.info(
            "llm.call_completed",
            provider=response.provider,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=ctx.latency_ms(),
            cost_usd=response.cost_usd,
            finish_reason=response.finish_reason,
            cache_hit=response.cached,
            fallback_used=fallback_used,
            fallback_provider=response.provider if fallback_used else None,
            attempts=ctx.attempts,
        )
        if response.finish_reason == "length" and not response.cached:
            log.warning(
                "llm.response_truncated",
                provider=response.provider,
                model=response.model,
                output_tokens=response.output_tokens,
            )

    def _raise_all_failed(self, ctx: _CallContext) -> NoReturn:
        log.error(
            "llm.call_failed",
            providers=self.provider_names,
            attempts=ctx.attempts,
            latency_ms=ctx.latency_ms(),
        )
        raise AllProvidersFailedError(
            f"Todos los proveedores LLM han fallado: {', '.join(self.provider_names)}"
        )


@lru_cache
def get_llm_gateway() -> LLMGateway:
    """Instancia única del wrapper (inyectada en los endpoints con Depends,
    lo que permite sustituirla por un doble en tests)."""
    settings = get_settings()
    cache = (
        TTLCache[LLMResponse](settings.llm_cache_ttl_seconds, settings.llm_cache_max_entries)
        if settings.llm_cache_enabled
        else None
    )
    return LLMGateway(
        build_providers(settings),
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
        cache=cache,
    )
