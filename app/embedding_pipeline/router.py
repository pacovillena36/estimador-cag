"""Router del pipeline de embeddings: POST /embeddings/ingest.

Orquesta chunker -> embedder y ensambla la respuesta. Los fallos de la API
de embeddings se traducen a 500 genérico en app/main.py (el detalle va a
los logs).
"""

from functools import lru_cache

import openai
from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError

from app.embedding_pipeline.chunker import JSONStructuralChunker
from app.embedding_pipeline.embedder import (
    MAX_INPUT_TOKENS,
    OpenAIEmbedder,
    estimate_cost_usd,
    get_pipeline_embedder,
)
from app.embedding_pipeline.schemas import IngestRequest, IngestResponse, IngestStats
from app.schemas import ErrorResponse

router = APIRouter(tags=["embeddings"])


class EmbeddingIngestError(Exception):
    """La API de embeddings falló durante la ingesta."""


@lru_cache
def get_chunker() -> JSONStructuralChunker:
    return JSONStructuralChunker()


@router.post(
    "/ingest",
    response_model=IngestResponse,
    responses={500: {"model": ErrorResponse, "description": "Fallo de la API de embeddings"}},
    summary="Trocea presupuestos (un componente = un chunk) y devuelve sus embeddings",
)
def ingest(
    request: IngestRequest,
    chunker: JSONStructuralChunker = Depends(get_chunker),
    embedder: OpenAIEmbedder = Depends(get_pipeline_embedder),
) -> IngestResponse:
    # Handler síncrono: FastAPI lo ejecuta en el threadpool, así que las
    # llamadas bloqueantes a la API (y las esperas de reintento) no
    # bloquean el event loop.
    chunks = chunker.chunk(request.budgets)

    # La API rechazaría la entrada entera: es un dato del cliente, no un
    # fallo del servicio, así que se responde 422 sin llamar a la API.
    oversized = [chunk.chunk_id for chunk in chunks if chunk.token_count > MAX_INPUT_TOKENS]
    if oversized:
        raise RequestValidationError(
            [
                {
                    "type": "chunk_too_large",
                    "loc": ("body", "budgets"),
                    "msg": f"Components exceed {MAX_INPUT_TOKENS} tokens: {', '.join(oversized)}",
                }
            ]
        )

    try:
        embedded = embedder.embed_many(chunks)
    except openai.OpenAIError as exc:
        raise EmbeddingIngestError(type(exc).__name__) from exc

    total_tokens = sum(chunk.token_count for chunk in chunks)
    return IngestResponse(
        chunks=embedded,
        stats=IngestStats(
            total_budgets=len(request.budgets),
            total_chunks=len(embedded),
            total_tokens=total_tokens,
            estimated_cost_usd=round(estimate_cost_usd(total_tokens), 8),
        ),
    )
