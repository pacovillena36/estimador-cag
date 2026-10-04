"""Dependencias inyectables de las sesiones (sustituibles en tests con
app.dependency_overrides)."""

from datetime import timedelta
from functools import lru_cache

from fastapi import Depends

from app.attachments import AttachmentLimits
from app.config import Settings, get_settings
from app.guardrails.pipeline import InputPipeline, OutputPipeline
from app.metadata_extractor import MetadataExtractor
from app.services.estimation_service import get_input_pipeline, get_output_pipeline
from app.services.llm_gateway import LLMGateway, get_llm_gateway
from app.services.session_estimation import SessionEstimationService
from app.sessions import SessionStore


@lru_cache
def get_session_store() -> SessionStore:
    """Almacén único del proceso (ver la nota de volatilidad en sessions.py)."""
    settings = get_settings()
    return SessionStore(
        max_turns=settings.max_turns,
        ttl=timedelta(minutes=settings.session_ttl_minutes),
        max_sessions=settings.max_sessions,
    )


def get_metadata_extractor(
    settings: Settings = Depends(get_settings),
    gateway: LLMGateway = Depends(get_llm_gateway),
) -> MetadataExtractor:
    return MetadataExtractor(gateway, max_tokens=settings.metadata_extractor_max_tokens)


def get_session_estimation_service(
    settings: Settings = Depends(get_settings),
    gateway: LLMGateway = Depends(get_llm_gateway),
    input_pipeline: InputPipeline = Depends(get_input_pipeline),
    output_pipeline: OutputPipeline = Depends(get_output_pipeline),
    extractor: MetadataExtractor = Depends(get_metadata_extractor),
) -> SessionEstimationService:
    return SessionEstimationService(
        gateway,
        input_pipeline,
        output_pipeline,
        extractor,
        prompt_version=settings.session_prompt_version,
        min_confidence_pct=settings.min_confidence_pct,
        attachment_limits=AttachmentLimits.from_settings(settings),
    )
