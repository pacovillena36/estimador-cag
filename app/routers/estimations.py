"""Router de estimaciones: expone el endpoint que genera una estimación de
software a partir de la transcripción de una reunión (arquitectura CAG)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings
from app.services.llm_service import generate_estimation

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
    estimation = generate_estimation(request.transcription)

    model = (
        settings.openai_model
        if settings.llm_provider == "openai"
        else settings.anthropic_model
    )

    return EstimationResponse(
        estimation=estimation,
        model=model,
        provider=settings.llm_provider,
    )
