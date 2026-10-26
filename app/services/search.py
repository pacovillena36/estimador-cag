"""Búsqueda semántica: embedding de la query (mismo modelo que la ingesta) y
top-k chunks por distancia coseno. search_time_ms mide ambas cosas."""

import time
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.embedding_pipeline.embedder import PipelineEmbedder
from app.repositories.chunks import ChunkRepository
from app.search_schemas import SearchResult

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SearchOutcome:
    results: list[SearchResult]
    search_time_ms: int


class SearchService:
    def __init__(self, session: AsyncSession, embedder: PipelineEmbedder) -> None:
        self._session = session
        self._chunks = ChunkRepository(session)
        self._embedder = embedder

    async def search(self, query: str, k: int) -> SearchOutcome:
        t0 = time.perf_counter()
        query_vector = await self._embedder.embed_query(query)
        async with self._session.begin():
            rows = await self._chunks.nearest(query_vector, k)
        search_time_ms = int((time.perf_counter() - t0) * 1000)

        results = [
            SearchResult(
                chunk_id=row.id,
                document_id=row.document_id,
                chunk_type=row.chunk_type,
                content=row.content,
                distance=row.distance,
                # Atributo ORM `meta` -> campo "metadata" de la API.
                metadata=row.meta,
            )
            for row in rows
        ]
        # Sin el texto de la query (puede contener datos del cliente).
        log.info("search.completed", k=k, results=len(results), search_time_ms=search_time_ms)
        return SearchOutcome(results, search_time_ms)
