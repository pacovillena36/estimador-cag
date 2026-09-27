"""Router de estimaciones: expone el endpoint que genera una estimación de
software a partir de la transcripción de una reunión (arquitectura CAG).

Los endpoints delegan en el wrapper de proveedores (LLMGateway) y trabajan
solo con su respuesta normalizada: no saben qué proveedor ha respondido ni
si la respuesta viene de caché.
"""

import json
from collections.abc import Iterator

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.llm_gateway import LLMGateway, ProviderError, get_llm_gateway
from app.services.llm_service import build_system_prompt

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/estimate", tags=["estimations"])


class InvalidEstimationError(Exception):
    """El modelo respondió, pero la respuesta no es una estimación válida."""


class EstimationRequest(BaseModel):
    # Se recortan espacios/saltos de línea al principio y al final: no
    # cambian el significado y, al pegar texto, suelen variar entre envíos,
    # lo que haría fallar la caché exact-match con la misma transcripción.
    model_config = ConfigDict(str_strip_whitespace=True)

    transcription: str = Field(
        ...,
        min_length=1,
        max_length=settings.max_transcription_chars,
        description="Transcripción (o resumen) de la reunión con el cliente.",
    )


class EstimationResponse(BaseModel):
    estimation: str = Field(..., description="Estimación generada por el modelo.")
    model: str = Field(..., description="Modelo LLM que generó la estimación.")
    provider: str = Field(..., description="Proveedor LLM que respondió.")
    cached: bool = Field(..., description="True si la respuesta viene de la caché.")
    cost_usd: float | None = Field(None, description="Coste estimado de la llamada (USD).")
    finish_reason: str | None = Field(
        None, description='"stop" si el modelo terminó; "length" si se truncó.'
    )


def _parse_estimation(text: str) -> str:
    """Normaliza y valida la estimación devuelta por el modelo."""
    estimation = text.strip()
    if not estimation:
        raise InvalidEstimationError("El modelo devolvió una estimación vacía.")
    return estimation


@router.post("", response_model=EstimationResponse)
def create_estimation(
    request: EstimationRequest,
    gateway: LLMGateway = Depends(get_llm_gateway),
) -> EstimationResponse:
    # Nunca se registra el contenido de la transcripción, solo su tamaño.
    log.info("estimation.requested", stream=False, chars=len(request.transcription))

    result = gateway.complete(build_system_prompt(), request.transcription)
    estimation = _parse_estimation(result.text)

    log.info("estimation.generated", cached=result.cached, chars=len(estimation))
    return EstimationResponse(
        estimation=estimation,
        model=result.model,
        provider=result.provider,
        cached=result.cached,
        cost_usd=result.cost_usd,
        finish_reason=result.finish_reason,
    )


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
def create_estimation_stream(
    request: EstimationRequest,
    gateway: LLMGateway = Depends(get_llm_gateway),
) -> StreamingResponse:
    """Misma generación que create_estimation, pero en streaming (SSE):
    emite un evento `delta` por cada fragmento de texto recibido del LLM y,
    al terminar, un evento `done` con las métricas de la llamada (modelo,
    tokens, tiempo de respuesta). Si el proveedor falla a mitad de la
    respuesta se emite un evento `error` en lugar de `done`.

    La conexión con el proveedor (y el fallback) ocurre antes de devolver
    la respuesta, así que si todos fallan el cliente recibe un 503 normal.
    """
    log.info("estimation.requested", stream=True, chars=len(request.transcription))

    llm_stream = gateway.stream(build_system_prompt(), request.transcription)

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
