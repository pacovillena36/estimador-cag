"""Servicio de estimación: orquesta guardrails, prompt y LLM.

El router solo traduce HTTP; toda la lógica vive aquí y cada componente
(wrapper LLM, cliente de moderación, pipelines) se inyecta con Depends, así
que se puede sustituir en los tests.

Flujo de `estimate`, en orden:
1. Capa 2: input_pipeline.run(description). Lanza InputRejectedError
   (-> 400) o ModerationUnavailableError (-> 503, fail-closed).
2. Capa 3: render del prompt con el input neutralizado y la sección de
   alcance.
3. Capas 4-5: Instructor con response_model=EstimationResult; los
   validadores (FIX_RETRY) reciben el umbral MIN_CONFIDENCE_PCT por el
   contexto de validación. Reintentos agotados -> InvalidStructuredOutputError
   (-> 502).
4. Capa 5: output_pipeline.run(result): out-of-scope y PII (FILTER).
5. EstimationResponse con out_of_scope y prompt_version.
"""

import structlog
from fastapi import Depends

from app.config import Settings, get_settings
from app.guardrails.moderation import ModerationClient, get_moderation_client
from app.guardrails.pipeline import (
    InputPipeline,
    OutputPipeline,
    build_input_pipeline,
    build_output_pipeline,
    description_hash,
)
from app.prompts.loader import render_estimation_prompt, validate_estimation_prompt_version
from app.schemas import (
    MIN_CONFIDENCE_CONTEXT_KEY,
    EstimationRequest,
    EstimationResponse,
    EstimationResult,
)
from app.services.llm_gateway import LLMGateway, get_llm_gateway

log = structlog.get_logger(__name__)


class EstimationService:
    def __init__(
        self,
        gateway: LLMGateway,
        input_pipeline: InputPipeline,
        output_pipeline: OutputPipeline,
        *,
        min_confidence_pct: int,
    ) -> None:
        self._gateway = gateway
        self._input = input_pipeline
        self._output = output_pipeline
        self._min_confidence_pct = min_confidence_pct

    def estimate(self, request: EstimationRequest, *, prompt_version: str) -> EstimationResponse:
        # Contexto de logs de toda la petición (también los eventos llm.* y
        # guardrail.*). De la descripción solo se registran tamaño y hash.
        structlog.contextvars.bind_contextvars(
            prompt_version=prompt_version,
            description_sha256=description_hash(request.description),
        )
        log.info(
            "estimation.requested",
            chars=len(request.description),
            project_type=request.project_type.value,
            detail_level=request.detail_level.value,
            output_format=request.output_format.value,
        )
        # Una versión inexistente es un error del cliente (-> 422): se
        # comprueba antes de gastar en moderación o en el LLM.
        validate_estimation_prompt_version(prompt_version)

        self._input.run(request.description)

        system, user = render_estimation_prompt(
            request, version=prompt_version, min_confidence_pct=self._min_confidence_pct
        )
        result = self._gateway.complete_structured(
            system,
            user,
            EstimationResult,
            context={MIN_CONFIDENCE_CONTEXT_KEY: self._min_confidence_pct},
        )
        result = self._output.run(result)

        response = EstimationResponse(result=result, prompt_version=prompt_version)
        log.info(
            "estimation.generated",
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
) -> EstimationService:
    return EstimationService(
        gateway,
        input_pipeline,
        output_pipeline,
        min_confidence_pct=settings.min_confidence_pct,
    )
