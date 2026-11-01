"""Servicio de estimación: orquesta guardrails, caché semántico, prompt y LLM.

El router solo traduce HTTP; toda la lógica vive aquí y cada componente
(wrapper LLM, moderación, pipelines, embeddings, caché) se inyecta con
Depends, así que se puede sustituir en los tests.

Flujo de `estimate`, en orden (el orden es parte del contrato):
1. Capa 2: input_pipeline.run(description). Lanza InputRejectedError
   (-> 400) o ModerationUnavailableError (-> 503, fail-closed). Un rechazo
   no toca ni el caché ni el LLM: un hit nunca se salta la moderación.
2. Caché semántico (si SEMANTIC_CACHE_MODE != off): bucket determinista +
   embedding de la descripción normalizada (una sola vez) + lookup. Un hit
   se re-valida con EstimationResult (Redis no es fuente de confianza). En
   ACTIVE, un hit válido se devuelve con cached=true sin llamar al LLM.
3. Capa 3: render del prompt con el input neutralizado y la sección de
   alcance.
4. Capas 4-5: Instructor con response_model=EstimationResult (FIX_RETRY)
   y output_pipeline (out-of-scope, PII). Si algo falla, se eleva el error
   y no se escribe nada en el caché.
5. Escritura en el caché: último paso, solo con el resultado que ha
   superado todos los guardrails, reutilizando el embedding del paso 2.

El caché es fail-open: cualquier fallo de Redis o de embeddings se
registra y la petición sigue por el LLM.
"""

import time
from dataclasses import dataclass

import structlog
from fastapi import Depends
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.embeddings import Embedder, EmbeddingError, get_embedder
from app.guardrails.moderation import ModerationClient, get_moderation_client
from app.guardrails.pipeline import (
    InputPipeline,
    OutputPipeline,
    build_input_pipeline,
    build_output_pipeline,
    description_hash,
)
from app.prompts.loader import render_estimation_prompt, validate_estimation_prompt_version
from app.request_context import RequestContext
from app.schemas import (
    MIN_CONFIDENCE_CONTEXT_KEY,
    EstimationRequest,
    EstimationResponse,
    EstimationResult,
)
from app.semantic_cache.bucket import build_bucket_key
from app.semantic_cache.factory import get_semantic_cache
from app.semantic_cache.normalization import normalize_description
from app.semantic_cache.ports import CacheMode, CacheUnavailableError, EstimationCache
from app.services.llm_gateway import LLMGateway, get_llm_gateway

log = structlog.get_logger(__name__)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


@dataclass
class _CacheLookup:
    """Estado del caché durante una petición: el bucket y el embedding se
    calculan una vez y se reutilizan en la escritura."""

    bucket: str | None = None
    vector: list[float] | None = None
    hit: EstimationResult | None = None
    distance: float | None = None


class EstimationService:
    def __init__(
        self,
        gateway: LLMGateway,
        input_pipeline: InputPipeline,
        output_pipeline: OutputPipeline,
        *,
        min_confidence_pct: int,
        cache: EstimationCache,
        embedder: Embedder,
        cache_mode: CacheMode,
    ) -> None:
        self._gateway = gateway
        self._input = input_pipeline
        self._output = output_pipeline
        self._min_confidence_pct = min_confidence_pct
        self._cache = cache
        self._embedder = embedder
        self._cache_mode = cache_mode

    @property
    def _validation_context(self) -> dict[str, int]:
        return {MIN_CONFIDENCE_CONTEXT_KEY: self._min_confidence_pct}

    def estimate(
        self,
        request: EstimationRequest,
        *,
        prompt_version: str,
        ctx: RequestContext,
    ) -> EstimationResponse:
        # Contexto de logs de toda la petición (también los eventos llm.*,
        # guardrail.* y semantic_cache.*). De la descripción solo se
        # registran tamaño y hash.
        structlog.contextvars.bind_contextvars(
            prompt_version=prompt_version,
            description_sha256=description_hash(request.description),
            project_type=request.project_type.value,
            cache_mode=self._cache_mode.value,
        )
        log.info(
            "estimation.requested",
            chars=len(request.description),
            detail_level=request.detail_level.value,
            output_format=request.output_format.value,
        )
        # Una versión inexistente es un error del cliente (-> 422): se
        # comprueba antes de gastar en moderación, embeddings o LLM.
        validate_estimation_prompt_version(prompt_version)

        # 1. Input guardrails SIEMPRE antes del caché.
        self._input.run(request.description)

        # 2. Lookup en el caché semántico (fail-open).
        lookup = self._lookup(request, prompt_version=prompt_version, ctx=ctx)
        if lookup.hit is not None and self._cache_mode is CacheMode.ACTIVE:
            return self._respond(lookup.hit, prompt_version=prompt_version, cached=True)

        # 3-4. Prompt + LLM + output guardrails. Si fallan, la excepción
        # sale de aquí y no se escribe nada en el caché.
        llm_start = time.perf_counter()
        system, user = render_estimation_prompt(
            request, version=prompt_version, min_confidence_pct=self._min_confidence_pct
        )
        result = self._gateway.complete_structured(
            system, user, EstimationResult, context=self._validation_context
        )
        result = self._output.run(result)
        llm_latency_ms = _elapsed_ms(llm_start)

        if lookup.hit is not None:  # solo en SHADOW
            self._log_shadow_comparison(lookup, result)

        # 5. Escritura: último paso y solo si todo validó.
        if lookup.bucket is not None and lookup.vector is not None:
            self._safe_store(lookup.bucket, lookup.vector, result)

        return self._respond(
            result, prompt_version=prompt_version, cached=False, llm_latency_ms=llm_latency_ms
        )

    # ------------------------------------------------------------- caché

    def _lookup(
        self, request: EstimationRequest, *, prompt_version: str, ctx: RequestContext
    ) -> _CacheLookup:
        lookup = _CacheLookup()
        if self._cache_mode is CacheMode.OFF:
            return lookup

        lookup.bucket = build_bucket_key(request, prompt_version=prompt_version, tenant_id=ctx.tenant_id)
        lookup.vector = self._safe_embed(normalize_description(request.description))
        if lookup.vector is None:
            return lookup

        start = time.perf_counter()
        fields = {"bucket": lookup.bucket[:16]}
        try:
            entry = self._cache.lookup(bucket=lookup.bucket, vector=lookup.vector)
        except CacheUnavailableError as exc:
            log.warning(
                "semantic_cache.lookup",
                result="error",
                error_type=str(exc),
                latency_ms=_elapsed_ms(start),
                **fields,
            )
            return lookup
        if entry is None:
            log.info("semantic_cache.lookup", result="miss", latency_ms=_elapsed_ms(start), **fields)
            return lookup

        # Defensa en profundidad: Redis no es fuente de confianza. Una
        # entrada que no valide (corrupta, manipulada o de un umbral de
        # confianza anterior) se trata como miss.
        try:
            hit = EstimationResult.model_validate_json(entry.response, context=self._validation_context)
        except ValidationError as exc:
            log.warning(
                "semantic_cache.lookup",
                result="invalid_entry",
                distance=round(entry.distance, 5),
                errors=exc.error_count(),
                latency_ms=_elapsed_ms(start),
                **fields,
            )
            return lookup

        lookup.hit, lookup.distance = hit, entry.distance
        log.info(
            "semantic_cache.lookup",
            result="hit",
            distance=round(entry.distance, 5),
            latency_ms=_elapsed_ms(start),
            **fields,
        )
        return lookup

    def _safe_embed(self, text: str) -> list[float] | None:
        start = time.perf_counter()
        try:
            vector = self._embedder.embed(text)
        except EmbeddingError as exc:
            log.warning("semantic_cache.embedding_failed", error_type=str(exc), latency_ms=_elapsed_ms(start))
            return None
        log.info("semantic_cache.embedding", latency_ms=_elapsed_ms(start))
        return vector

    def _safe_store(self, bucket: str, vector: list[float], result: EstimationResult) -> None:
        start = time.perf_counter()
        try:
            self._cache.store(bucket=bucket, vector=vector, response=result.model_dump_json())
        except CacheUnavailableError as exc:
            log.warning(
                "semantic_cache.store",
                result="error",
                error_type=str(exc),
                bucket=bucket[:16],
                latency_ms=_elapsed_ms(start),
            )
            return
        log.info("semantic_cache.store", result="ok", bucket=bucket[:16], latency_ms=_elapsed_ms(start))

    @staticmethod
    def _log_shadow_comparison(lookup: _CacheLookup, llm_result: EstimationResult) -> None:
        """En SHADOW, compara el hit con lo que ha respondido el LLM para
        validar con datos que el threshold no produce falsos positivos."""
        cached = lookup.hit
        assert cached is not None
        base = max(llm_result.total_hours, 1)
        log.info(
            "semantic_cache.shadow_comparison",
            distance=round(lookup.distance or 0.0, 5),
            out_of_scope_match=cached.is_out_of_scope == llm_result.is_out_of_scope,
            total_hours_cached=cached.total_hours,
            total_hours_llm=llm_result.total_hours,
            total_hours_diff_pct=round(abs(cached.total_hours - llm_result.total_hours) * 100 / base, 1),
            phases_cached=len(cached.phases),
            phases_llm=len(llm_result.phases),
        )

    # --------------------------------------------------------- respuesta

    @staticmethod
    def _respond(
        result: EstimationResult,
        *,
        prompt_version: str,
        cached: bool,
        llm_latency_ms: float | None = None,
    ) -> EstimationResponse:
        response = EstimationResponse(result=result, prompt_version=prompt_version, cached=cached)
        log.info(
            "estimation.generated",
            cached=cached,
            llm_latency_ms=llm_latency_ms,
            out_of_scope=response.out_of_scope,
            phases=len(result.phases),
            total_hours=result.total_hours,
            total_duration_weeks=result.total_duration_weeks,
            total_cost_eur=result.total_cost_eur,
            confidence_pct=result.confidence_pct,
        )
        return response


def get_input_pipeline(
    settings: Settings = Depends(get_settings),
    moderation: ModerationClient = Depends(get_moderation_client),
) -> InputPipeline:
    return build_input_pipeline(settings, moderation)


def get_output_pipeline(settings: Settings = Depends(get_settings)) -> OutputPipeline:
    return build_output_pipeline(settings)


def get_estimation_service(
    settings: Settings = Depends(get_settings),
    gateway: LLMGateway = Depends(get_llm_gateway),
    input_pipeline: InputPipeline = Depends(get_input_pipeline),
    output_pipeline: OutputPipeline = Depends(get_output_pipeline),
    cache: EstimationCache = Depends(get_semantic_cache),
    embedder: Embedder = Depends(get_embedder),
) -> EstimationService:
    return EstimationService(
        gateway,
        input_pipeline,
        output_pipeline,
        min_confidence_pct=settings.min_confidence_pct,
        cache=cache,
        embedder=embedder,
        cache_mode=settings.semantic_cache_mode,
    )
