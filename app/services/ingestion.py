"""Ingesta de un presupuesto: chunker -> embedder -> Postgres, atómica.

Orden: check rápido de duplicado (409), chunking, embeddings de todos los
chunks y, solo entonces, una transacción corta que inserta el documento y
sus chunks. La llamada a OpenAI (cientos de ms a segundos) se hace sin
ninguna transacción abierta, así no retiene una conexión del pool; si
falla, no se ha escrito nada. La restricción única de source_path cubre la
carrera entre dos ingestas simultáneas (IntegrityError -> 409).
"""

import time
from dataclasses import dataclass

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Document
from app.embedding_pipeline.chunker import CHUNK_TYPE, JSONStructuralChunker
from app.embedding_pipeline.embedder import MAX_INPUT_TOKENS, PipelineEmbedder, estimate_cost_usd
from app.embedding_pipeline.schemas import IngestRequest
from app.repositories.documents import DocumentRepository

log = structlog.get_logger(__name__)


class DocumentAlreadyIngestedError(Exception):
    def __init__(self, document_id: int) -> None:
        super().__init__(f"document {document_id} already ingested")
        self.document_id = document_id


class InvalidDocumentError(Exception):
    """El documento es válido como JSON pero no se puede ingestar (dato del
    cliente, no fallo del servicio): se responde 422 sin llamar a la API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IngestResult:
    document_id: int
    chunks_created: int
    ingestion_time_ms: int


class IngestionService:
    def __init__(
        self,
        session: AsyncSession,
        chunker: JSONStructuralChunker,
        embedder: PipelineEmbedder,
        *,
        max_chunks: int,
    ) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._chunker = chunker
        self._embedder = embedder
        self._max_chunks = max_chunks

    async def ingest(self, request: IngestRequest) -> IngestResult:
        t0 = time.perf_counter()

        async with self._session.begin():
            existing_id = await self._documents.find_id_by_source_path(request.source_path)
        if existing_id is not None:
            raise DocumentAlreadyIngestedError(existing_id)

        budget = request.content
        chunks = self._chunker.chunk([budget])
        if len(chunks) > self._max_chunks:
            raise InvalidDocumentError(
                "too_many_chunks", f"Document produces {len(chunks)} chunks (max {self._max_chunks})"
            )
        oversized = [chunk.chunk_id for chunk in chunks if chunk.token_count > MAX_INPUT_TOKENS]
        if oversized:
            raise InvalidDocumentError(
                "chunk_too_large", f"Components exceed {MAX_INPUT_TOKENS} tokens: {', '.join(oversized)}"
            )

        # Fuera de cualquier transacción (ver docstring del módulo).
        embed_start = time.perf_counter()
        vectors = await self._embedder.embed_chunks(chunks)
        embedding_ms = round((time.perf_counter() - embed_start) * 1000, 1)

        document = Document(
            source_path=request.source_path,
            document_type=request.document_type,
            meta={
                "budget_id": budget.budget_id,
                "client_sector": budget.client_metadata.sector,
                "country": budget.client_metadata.country,
                "main_technology": budget.main_technology,
                "year": budget.year,
                "total_estimated_hours": budget.total_estimated_hours,
            },
            chunks=[
                Chunk(chunk_type=CHUNK_TYPE, content=chunk.text, embedding=vector, meta=chunk.metadata)
                for chunk, vector in zip(chunks, vectors, strict=True)
            ],
        )
        try:
            async with self._session.begin():
                document_id = await self._documents.add(document)
        except IntegrityError:
            # Otra ingesta del mismo source_path ganó la carrera.
            async with self._session.begin():
                existing_id = await self._documents.find_id_by_source_path(request.source_path)
            if existing_id is None:
                raise
            raise DocumentAlreadyIngestedError(existing_id) from None

        ingestion_time_ms = int((time.perf_counter() - t0) * 1000)
        tokens = sum(chunk.token_count for chunk in chunks)
        log.info(
            "embeddings.document_ingested",
            document_id=document_id,
            document_type=request.document_type,
            chunks_created=len(chunks),
            tokens=tokens,
            estimated_cost_usd=round(estimate_cost_usd(tokens), 8),
            embedding_ms=embedding_ms,
            ingestion_time_ms=ingestion_time_ms,
        )
        return IngestResult(document_id, len(chunks), ingestion_time_ms)
