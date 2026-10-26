"""Raíz de composición de las sesiones y del pipeline de embeddings
(ingesta y búsqueda): aquí, y solo aquí, se conectan los puertos con sus
adaptadores. Las dependencias de FastAPI son sustituibles en
tests con app.dependency_overrides; `build_session_estimation_service` sirve
también fuera de FastAPI (runner de evals)."""

from datetime import timedelta
from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import AttachmentLimits
from app.config import Settings, get_settings
from app.conversation.adapters.llm_metadata_extractor import LLMMetadataExtractor
from app.conversation.adapters.llm_review import LLMBoss, LLMCritic
from app.conversation.adapters.llm_summarizer import LLMConversationSummarizer
from app.conversation.application.estimation_engine import EstimationEngine
from app.conversation.application.memory_maintainer import MemoryMaintainer
from app.conversation.domain.tiering import TierPolicy
from app.db.session import get_session
from app.embedding_pipeline.chunker import JSONStructuralChunker
from app.embedding_pipeline.embedder import PipelineEmbedder, get_pipeline_embedder
from app.guardrails.pipeline import InputPipeline, OutputPipeline
from app.ports.llm import StructuredLLM
from app.services.estimation_service import get_input_pipeline, get_output_pipeline
from app.services.ingestion import IngestionService
from app.services.llm_gateway import get_llm_gateway
from app.services.session_estimation import SessionEstimationService
from app.sessions import SessionStore


def build_session_store(settings: Settings) -> SessionStore:
    return SessionStore(
        max_turns=settings.max_conversation_turns,
        ttl=timedelta(minutes=settings.session_ttl_minutes),
        max_sessions=settings.max_sessions,
        max_anchors=settings.max_anchors,
    )


@lru_cache
def get_session_store() -> SessionStore:
    """Almacén único del proceso (ver la nota de volatilidad en sessions.py)."""
    return build_session_store(get_settings())


def build_tier_policy(settings: Settings) -> TierPolicy:
    return TierPolicy(
        enabled=settings.tier_mode == "dynamic",
        attachment_chars_threshold=settings.tier_attachment_chars_threshold,
        transcript_chars_threshold=settings.tier_transcript_chars_threshold,
        low_confidence_pct=settings.tier_low_confidence_pct,
    )


def build_session_estimation_service(
    settings: Settings,
    llm: StructuredLLM,
    input_pipeline: InputPipeline,
    output_pipeline: OutputPipeline,
) -> SessionEstimationService:
    min_confidence = settings.min_confidence_pct
    engine = EstimationEngine(
        llm,
        LLMCritic(llm, min_confidence_pct=min_confidence),
        LLMBoss(llm, min_confidence_pct=min_confidence),
        min_confidence_pct=min_confidence,
    )
    memory = MemoryMaintainer(
        LLMConversationSummarizer(llm, max_tokens=settings.summarizer_max_tokens),
        LLMMetadataExtractor(llm, max_tokens=settings.metadata_extractor_max_tokens),
    )
    return SessionEstimationService(
        input_pipeline,
        output_pipeline,
        engine,
        memory,
        build_tier_policy(settings),
        prompt_version=settings.session_prompt_version,
        min_confidence_pct=min_confidence,
        attachment_limits=AttachmentLimits.from_settings(settings),
        default_mode=settings.estimation_mode,
    )


def get_session_estimation_service(
    settings: Settings = Depends(get_settings),
    llm: StructuredLLM = Depends(get_llm_gateway),
    input_pipeline: InputPipeline = Depends(get_input_pipeline),
    output_pipeline: OutputPipeline = Depends(get_output_pipeline),
) -> SessionEstimationService:
    return build_session_estimation_service(settings, llm, input_pipeline, output_pipeline)


# --------------------------------------------- pipeline de embeddings (pgvector)


class PayloadTooLargeError(Exception):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"body exceeds {max_bytes} bytes")
        self.max_bytes = max_bytes


async def limit_ingest_body_size(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """413 si el body supera INGEST_MAX_BODY_BYTES (Content-Length declarado
    o, si no lo hay, lo realmente recibido)."""
    max_bytes = settings.ingest_max_body_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise PayloadTooLargeError(max_bytes)
    if len(await request.body()) > max_bytes:
        raise PayloadTooLargeError(max_bytes)


@lru_cache
def get_chunker() -> JSONStructuralChunker:
    return JSONStructuralChunker(get_settings().embedding_model)


def get_ingestion_service(
    session: AsyncSession = Depends(get_session),
    chunker: JSONStructuralChunker = Depends(get_chunker),
    embedder: PipelineEmbedder = Depends(get_pipeline_embedder),
    settings: Settings = Depends(get_settings),
) -> IngestionService:
    return IngestionService(session, chunker, embedder, max_chunks=settings.ingest_max_chunks)

