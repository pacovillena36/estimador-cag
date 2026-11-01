"""Tests de integración contra el Postgres (pgvector) de docker-compose, en
una BD de test separada y con el embedder falso (nunca llaman a OpenAI)."""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.models import Chunk, Document
from app.db.session import create_sessionmaker, get_session
from app.dependencies import get_chunker
from app.embedding_pipeline.embedder import get_pipeline_embedder
from app.embedding_pipeline.schemas import Budget
from app.main import app
from tests.pgvector.conftest import ingest_body

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def sessionmaker(test_database_url):
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE documents, chunks RESTART IDENTITY CASCADE"))
    yield create_sessionmaker(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(sessionmaker, fake_pipeline_embedder):
    async def session_override():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_pipeline_embedder] = lambda: fake_pipeline_embedder
    # ASGITransport no ejecuta el lifespan: ni claves reales ni engine de la app.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


async def _counts(sessionmaker) -> tuple[int, int]:
    async with sessionmaker() as session:
        documents = await session.scalar(select(func.count()).select_from(Document))
        chunks = await session.scalar(select(func.count()).select_from(Chunk))
    return documents, chunks


async def test_ingest_creates_one_document_and_its_chunks(client, sessionmaker, sample_budgets):
    budget = sample_budgets[0]
    response = await client.post("/embeddings/ingest", json=ingest_body(budget))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"document_id", "chunks_created", "embedding_dimension", "ingestion_time_ms"}
    assert body["chunks_created"] == len(budget["components"])
    assert body["embedding_dimension"] == 1536
    assert await _counts(sessionmaker) == (1, len(budget["components"]))
    async with sessionmaker() as session:
        missing = await session.scalar(select(func.count()).where(Chunk.embedding.is_(None)))
        document = await session.get(Document, body["document_id"])
    assert missing == 0
    assert document.meta["budget_id"] == budget["budget_id"]


async def test_duplicate_ingest_returns_409_with_existing_id(client, sessionmaker, sample_budgets):
    first = await client.post("/embeddings/ingest", json=ingest_body(sample_budgets[0]))
    counts = await _counts(sessionmaker)

    second = await client.post("/embeddings/ingest", json=ingest_body(sample_budgets[0]))

    assert second.status_code == 409
    assert second.json() == {"detail": "Document already ingested", "document_id": first.json()["document_id"]}
    assert await _counts(sessionmaker) == counts


async def test_embedder_failure_leaves_no_rows(client, sessionmaker, sample_budgets, fake_pipeline_embedder):
    fake_pipeline_embedder.failing = True

    response = await client.post("/embeddings/ingest", json=ingest_body(sample_budgets[0]))

    assert response.status_code == 502
    assert await _counts(sessionmaker) == (0, 0)


async def test_too_many_chunks_is_rejected_before_embedding(
    client, sessionmaker, sample_budgets, fake_pipeline_embedder
):
    settings = get_settings().model_copy(update={"ingest_max_chunks": 2})
    app.dependency_overrides[get_settings] = lambda: settings

    response = await client.post("/embeddings/ingest", json=ingest_body(sample_budgets[0]))

    assert response.status_code == 422
    assert fake_pipeline_embedder.calls == 0
    assert await _counts(sessionmaker) == (0, 0)


async def test_search_returns_k_results_sorted_by_distance(client, sample_budgets):
    for budget in sample_budgets[:4]:
        assert (await client.post("/embeddings/ingest", json=ingest_body(budget))).status_code == 200
    # El texto exacto de un chunk da su mismo vector: debe salir primero.
    target = get_chunker().chunk([Budget.model_validate(sample_budgets[2])])[1]

    response = await client.post("/search", json={"query": target.text, "k": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["k"] == 3
    assert isinstance(body["search_time_ms"], int)
    results = body["results"]
    assert len(results) == 3
    distances = [result["distance"] for result in results]
    assert distances == sorted(distances)
    assert all(0 <= distance <= 2 for distance in distances)
    assert results[0]["content"] == target.text
    assert results[0]["distance"] == pytest.approx(0, abs=1e-6)
    assert results[0]["metadata"]["component_id"] == target.metadata["component_id"]
    assert set(results[0]) == {"chunk_id", "document_id", "chunk_type", "content", "distance", "metadata"}


async def test_search_respects_k_when_fewer_chunks_exist(client, sample_budgets):
    await client.post("/embeddings/ingest", json=ingest_body(sample_budgets[0]))

    response = await client.post("/search", json={"query": "oauth", "k": 50})

    assert len(response.json()["results"]) == len(sample_budgets[0]["components"])

