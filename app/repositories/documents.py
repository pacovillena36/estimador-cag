from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_id_by_source_path(self, source_path: str) -> int | None:
        stmt = select(Document.id).where(Document.source_path == source_path)
        return await self._session.scalar(stmt)

    async def add(self, document: Document) -> int:
        """Inserta el documento con sus chunks (cascade de la relación) y
        devuelve su id. El commit lo hace quien abrió la transacción."""
        self._session.add(document)
        await self._session.flush()
        return document.id
