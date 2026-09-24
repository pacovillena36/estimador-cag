"""Router de estimaciones: expone el endpoint que genera una estimación de
software a partir de la transcripción de una reunión (arquitectura CAG)."""

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.llm_service import generate_estimation, generate_estimation_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/estimate", tags=["estimations"])


class EstimationRequest(BaseModel):
    transcription: str = Field(
        ...,
        min_length=1,
        description="Transcripción (o resumen) de la reunión con el cliente.",
    )


class EstimationResponse(BaseModel):
    estimation: str = Field(..., description="Estimación generada por el modelo.")
    model: str = Field(..., description="Modelo LLM usado para generar la estimación.")
    provider: str = Field(..., description="Proveedor LLM usado (openai o anthropic).")


@router.post("", response_model=EstimationResponse)
def create_estimation(request: EstimationRequest) -> EstimationResponse:
    logger.info(
        "Nueva solicitud de estimación (provider=%s, %d caracteres)",
        settings.llm_provider,
        len(request.transcription),
    )
    logger.debug("Transcripción recibida: %s", request.transcription)

    estimation = generate_estimation(request.transcription)

    model = (
        settings.openai_model
        if settings.llm_provider == "openai"
        else settings.anthropic_model
    )

    logger.info("Estimación generada correctamente (model=%s)", model)

    return EstimationResponse(
        estimation=estimation,
        model=model,
        provider=settings.llm_provider,
    )


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
def create_estimation_stream(request: EstimationRequest) -> StreamingResponse:
    """Misma generación que create_estimation, pero en streaming (SSE):
    emite un evento `delta` por cada fragmento de texto recibido del LLM y,
    al terminar, un evento `done` con las métricas de la llamada (modelo,
    tokens, tiempo de respuesta)."""
    logger.info(
        "Nueva solicitud de estimación en streaming (provider=%s, %d caracteres)",
        settings.llm_provider,
        len(request.transcription),
    )

    chunks, metrics = generate_estimation_stream(request.transcription)

    def event_stream() -> Iterator[str]:
        for delta in chunks:
            yield _sse_event("delta", {"delta": delta})
        yield _sse_event(
            "done",
            {
                "provider": metrics.provider,
                "model": metrics.model,
                "input_tokens": metrics.input_tokens,
                "output_tokens": metrics.output_tokens,
                "elapsed_seconds": metrics.elapsed_seconds,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
