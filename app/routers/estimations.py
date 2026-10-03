"""Router de estimaciones: genera una estimación de software a partir de la
descripción de un proyecto y de las opciones del formulario del cliente.

Flujo: EstimationRequest -> render_estimation_prompt() -> (system, user)
-> wrapper de proveedores (LLMGateway.complete_structured) ->
EstimationResult validado -> EstimationResponse. El endpoint no parsea
nada: recibe directamente una instancia tipada, o una excepción si la
respuesta del modelo no cumple el contrato. No sabe qué proveedor ha
respondido ni si la respuesta viene de caché.

El servicio devuelve siempre la estimación estructurada, nunca su
presentación: `output_format` es solo una pista para el contenido, y cómo
se pinta (tabla, lista, narrativa...) lo decide el cliente.
"""

import structlog
from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, EstimationResponse, EstimationResult
from app.services.llm_gateway import LLMGateway, get_llm_gateway

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/estimate", tags=["estimations"])


def get_prompt_version(
    prompt_version: str | None = Query(
        default=None,
        pattern=r"^v\d+$",
        description=(
            "Versión del prompt a usar (p. ej. v2). Si se omite, se usa la "
            "configurada en PROMPT_VERSION."
        ),
    ),
) -> str:
    return prompt_version or settings.prompt_version


def _render_prompt(request: EstimationRequest, *, version: str) -> tuple[str, str]:
    # prompt_version y las opciones del formulario quedan enlazadas al
    # contexto de logs: aparecen también en los eventos llm.* del wrapper.
    # Nunca se registra el texto de la descripción, solo su tamaño.
    structlog.contextvars.bind_contextvars(prompt_version=version)
    log.info(
        "estimation.requested",
        chars=len(request.description),
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
    )
    # Si la versión no existe lanza PromptVersionNotFoundError (-> 422)
    # antes de llamar al modelo.
    return render_estimation_prompt(request, version=version)


@router.post("", response_model=EstimationResponse)
def create_estimation(
    request: EstimationRequest,
    gateway: LLMGateway = Depends(get_llm_gateway),
    prompt_version: str = Depends(get_prompt_version),
) -> EstimationResponse:
    system, user = _render_prompt(request, version=prompt_version)

    # Si la respuesta no valida ni tras los reintentos, el wrapper eleva
    # InvalidStructuredOutputError (-> 502): nunca llega un dato a medias.
    result: EstimationResult = gateway.complete_structured(system, user, EstimationResult)

    log.info(
        "estimation.generated",
        phases=len(result.phases),
        total_hours=result.total_hours,
        total_duration_weeks=result.total_duration_weeks,
        total_cost_eur=result.total_cost_eur,
    )
    return EstimationResponse(result=result, prompt_version=prompt_version)
