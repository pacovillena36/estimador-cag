"""Router de estimaciones: solo traduce HTTP. La orquestación (guardrails,
prompt, LLM) vive en EstimationService, inyectado con Depends.

El servicio devuelve siempre la estimación estructurada, nunca su
presentación: `output_format` es solo una pista para el contenido, y cómo
se pinta (tabla, lista, narrativa...) lo decide el cliente. Los errores se
traducen a HTTP en los exception handlers de app/main.py.
"""

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError

from app.config import Settings, get_settings
from app.schemas import EstimationRequest, EstimationResponse, ErrorResponse
from app.services.estimation_service import EstimationService, get_estimation_service

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
    settings: Settings = Depends(get_settings),
) -> str:
    return prompt_version or settings.prompt_version


def validated_request(
    request: EstimationRequest,
    settings: Settings = Depends(get_settings),
) -> EstimationRequest:
    """Capa 1: el schema fija un techo absoluto de longitud; aquí se aplica
    el límite configurable (DESCRIPTION_MAX_LENGTH) con el mismo error 422
    que daría el propio schema."""
    max_length = settings.description_max_length
    if len(request.description) > max_length:
        raise RequestValidationError(
            [
                {
                    "type": "string_too_long",
                    "loc": ("body", "description"),
                    "msg": f"String should have at most {max_length} characters",
                    "ctx": {"max_length": max_length},
                }
            ]
        )
    return request


@router.post(
    "",
    response_model=EstimationResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Descripción rechazada por un guardrail (input_rejected)."},
        502: {"model": ErrorResponse, "description": "El modelo no devolvió una estimación válida (estimation_failed)."},
        503: {
            "model": ErrorResponse,
            "description": "Moderación no disponible (moderation_unavailable) o proveedores LLM caídos (llm_unavailable).",
        },
    },
)
def create_estimation(
    request: EstimationRequest = Depends(validated_request),
    prompt_version: str = Depends(get_prompt_version),
    service: EstimationService = Depends(get_estimation_service),
) -> EstimationResponse:
    return service.estimate(request, prompt_version=prompt_version)
