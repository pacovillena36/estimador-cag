"""Engine y sesiones async.

El engine se crea una sola vez en el lifespan de FastAPI (y se cierra al
apagar); se guarda en app.state junto a su sessionmaker. Cada request
recibe su propia AsyncSession vía `get_session`, que la cierra siempre.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    # create_async_engine no conecta: la primera conexión se abre al usarlo.
    return create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Dependencia FastAPI: una AsyncSession por request."""
    async with request.app.state.db_sessionmaker() as session:
        yield session
