"""Router de estimaciones: genera una estimación de software a partir de la
descripción de un proyecto y de las opciones del formulario del cliente.

Flujo: EstimationRequest -> render_estimation_prompt() -> (system, user)
-> wrapper de proveedores (LLMGateway) -> EstimationResponse. Los endpoints
trabajan solo con la respuesta normalizada del wrapper: no saben qué
proveedor ha respondido ni si la respuesta viene de caché.
"""

import json
from collections.abc import Iterator

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.config import settings
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, EstimationResponse
from app.services.llm_gateway import LLMGateway, ProviderError, get_llm_gateway

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/estimate", tags=["estimations"])


class InvalidEstimationError(Exception):
    """El modelo respondió, pero la respuesta no es una estimación válida."""


def _parse_estimation(text: str) -> str:
    """Normaliza y valida la estimación devuelta por el modelo."""
    estimation = text.strip()
    if not estimation:
        raise InvalidEstimationError("El modelo devolvió una estimación vacía.")
    return estimation


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


def _render_prompt(
    request: EstimationRequest, *, version: str, stream: bool
) -> tuple[str, str]:
    # prompt_version y las opciones del formulario quedan enlazadas al
    # contexto de logs: aparecen también en los eventos llm.* del wrapper.
    # Nunca se registra el texto de la descripción, solo su tamaño.
    structlog.contextvars.bind_contextvars(prompt_version=version)
    log.info(
        "estimation.requested",
        stream=stream,
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
    system, user = _render_prompt(request, version=prompt_version, stream=False)

    result = gateway.complete(system, user)
    text = _parse_estimation(result.text)

    log.info("estimation.generated", cached=result.cached, chars=len(text))
    return EstimationResponse(text=text, prompt_version=prompt_version)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
def create_estimation_stream(
    request: EstimationRequest,
    gateway: LLMGateway = Depends(get_llm_gateway),
    prompt_version: str = Depends(get_prompt_version),
) -> StreamingResponse:
    """Misma generación que create_estimation, pero en streaming (SSE):
    emite un evento `delta` por cada fragmento de texto recibido del LLM y,
    al terminar, un evento `done` con prompt_version y las métricas de la
    llamada. Si el proveedor falla a mitad de la respuesta se emite un
    evento `error` en lugar de `done`.

    La conexión con el proveedor (y el fallback) ocurre antes de devolver
    la respuesta, así que si todos fallan el cliente recibe un 503 normal.
    """
    system, user = _render_prompt(request, version=prompt_version, stream=True)
    llm_stream = gateway.stream(system, user)

    def event_stream() -> Iterator[str]:
        try:
            for delta in llm_stream:
                yield _sse_event("delta", {"delta": delta})
        except ProviderError:
            # Detalle ya registrado por el wrapper; al cliente solo un
            # mensaje genérico, sin información interna del proveedor.
            yield _sse_event(
                "error",
                {"detail": "El proveedor de IA interrumpió la respuesta. Inténtalo de nuevo."},
            )
            return

        result = llm_stream.response
        yield _sse_event(
            "done",
            {
                "prompt_version": prompt_version,
                "provider": result.provider,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "elapsed_seconds": result.elapsed_seconds,
                "cost_usd": result.cost_usd,
                "finish_reason": result.finish_reason,
                "cached": result.cached,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
