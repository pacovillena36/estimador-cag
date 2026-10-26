from collections.abc import Sequence

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def nearest(self, query_vector: Sequence[float], k: int) -> Sequence[Row]:
        """Los k chunks más cercanos por distancia coseno (operador <=>).

        Sin filtros por chunk_type ni metadata y sin índice vectorial: es un
        sequential scan a propósito (baseline de medición).
        """
        distance = Chunk.embedding.cosine_distance(query_vector)
        stmt = (
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.chunk_type,
                Chunk.content,
                Chunk.meta,
                distance.label("distance"),
            )
            .where(Chunk.embedding.is_not(None))
            .order_by(distance)
            .limit(k)
        )
        result = await self._session.execute(stmt)
        return result.all()
