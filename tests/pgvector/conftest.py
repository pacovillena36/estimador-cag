"""Fixtures de la persistencia vectorial: embedder falso (sin red) y una BD
de test separada en el Postgres de docker-compose.

TEST_DATABASE_URL apunta a la BD de test (por defecto estimator_test en
127.0.0.1:5432); se crea si no existe y se migra con Alembic. Si Postgres no
está disponible, los tests de integración se saltan.
"""

import asyncio
import hashlib
import json
import math
import os
from collections.abc import Sequence
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.db.models import EMBEDDING_DIM
from app.embedding_pipeline.embedder import EmbeddingProviderError
from app.embedding_pipeline.schemas import Chunk

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://estimator:estimator@127.0.0.1:5432/estimator_test"
)


def fake_vector(text: str) -> list[float]:
    """Vector determinista y normalizado de EMBEDDING_DIM dimensiones."""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((seed[i % len(seed)] * (i + 1)) % 251) / 250 - 0.5 for i in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


class FakePipelineEmbedder:
    """Sustituye al OpenAIEmbedder. `failing` simula un fallo del proveedor."""

    def __init__(self) -> None:
        self.failing = False
        self.calls = 0

    async def embed_chunks(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        self.calls += 1
        if self.failing:
            raise EmbeddingProviderError("APITimeoutError")
        return [fake_vector(chunk.text) for chunk in chunks]

    async def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        if self.failing:
            raise EmbeddingProviderError("APITimeoutError")
        return fake_vector(text)


@pytest.fixture
def fake_pipeline_embedder() -> FakePipelineEmbedder:
    return FakePipelineEmbedder()


@pytest.fixture(scope="session")
def sample_budgets() -> list[dict]:
    return json.loads((ROOT / "data" / "budgets_sample.json").read_text(encoding="utf-8"))


def ingest_body(budget: dict) -> dict:
    return {
        "source_path": f"data/budgets_sample.json#{budget['budget_id']}",
        "document_type": "historical_budget",
        "content": budget,
    }


async def _create_database_if_missing(url: str) -> None:
    parsed = make_url(url)
    admin = await asyncpg.connect(
        host=parsed.host,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        database="postgres",
        timeout=5,
    )
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", parsed.database)
        if not exists:
            # Identificador fijo de la configuración de test, no dato de usuario.
            await admin.execute(f'CREATE DATABASE "{parsed.database}"')
    finally:
        await admin.close()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    try:
        asyncio.run(_create_database_if_missing(TEST_DATABASE_URL))
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
        pytest.skip(f"Postgres de test no disponible: {type(exc).__name__}")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(config, "head")
    return TEST_DATABASE_URL
