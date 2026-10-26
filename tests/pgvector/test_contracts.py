"""Tests unitarios (sin red ni BD): validación de schemas de /search e
ingesta y formato exacto de las respuestas 409 y 502."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import get_settings
from app.dependencies import get_ingestion_service, get_search_service
from app.embedding_pipeline.embedder import EmbeddingProviderError
from app.embedding_pipeline.schemas import IngestRequest
from app.main import app
from app.search_schemas import SearchRequest
from app.services.ingestion import DocumentAlreadyIngestedError
from tests.pgvector.conftest import ingest_body


@pytest.mark.parametrize("k", [0, -1, get_settings().search_max_k + 1, 1000])
def test_search_rejects_k_out_of_range(k):
    with pytest.raises(ValidationError):
        SearchRequest(query="oauth backend", k=k)


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_search_rejects_empty_query(query):
    with pytest.raises(ValidationError):
        SearchRequest(query=query)


def test_search_rejects_too_long_query():
    with pytest.raises(ValidationError):
        SearchRequest(query="a" * (get_settings().search_query_max_length + 1))


def test_search_defaults_k_and_strips_query():
    request = SearchRequest(query="  oauth backend  ")
    assert request.k == 5
    assert request.query == "oauth backend"


def test_ingest_rejects_document_type_over_50_chars(sample_budgets):
    body = ingest_body(sample_budgets[0]) | {"document_type": "x" * 51}
    with pytest.raises(ValidationError):
        IngestRequest(**body)


@pytest.mark.parametrize("source_path", ["", "a\x00b", "line\nbreak", "x" * 1025])
def test_ingest_rejects_invalid_source_path(sample_budgets, source_path):
    with pytest.raises(ValidationError):
        IngestRequest(**(ingest_body(sample_budgets[0]) | {"source_path": source_path}))


def test_ingest_rejects_empty_content(sample_budgets):
    with pytest.raises(ValidationError):
        IngestRequest(**(ingest_body(sample_budgets[0]) | {"content": {}}))


class _RaisingService:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def ingest(self, request):
        raise self._exc

    async def search(self, query, k):
        raise self._exc


@pytest.fixture
def client_raising():
    def build(exc: Exception) -> TestClient:
        service = _RaisingService(exc)
        app.dependency_overrides[get_ingestion_service] = lambda: service
        app.dependency_overrides[get_search_service] = lambda: service
        # Sin `with`: no se ejecuta el lifespan (ni claves ni BD reales).
        return TestClient(app, raise_server_exceptions=False)

    yield build
    app.dependency_overrides.clear()


def test_conflict_body_is_exactly_detail_and_document_id(client_raising, sample_budgets):
    client = client_raising(DocumentAlreadyIngestedError(42))
    response = client.post("/embeddings/ingest", json=ingest_body(sample_budgets[0]))
    assert response.status_code == 409
    assert response.json() == {"detail": "Document already ingested", "document_id": 42}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/embeddings/ingest", None),
        ("post", "/search", {"query": "oauth backend", "k": 3}),
    ],
)
def test_embedding_provider_failure_is_generic_502(client_raising, sample_budgets, method, path, body):
    client = client_raising(EmbeddingProviderError("AuthenticationError"))
    response = client.post(path, json=body or ingest_body(sample_budgets[0]))
    assert response.status_code == 502
    assert response.json()["error"]["message"] == "Embedding provider error"
    assert "AuthenticationError" not in response.text


def test_ingest_rejects_oversized_body(client_raising):
    client = client_raising(AssertionError("no debería llegar al servicio"))
    padding = "a" * (get_settings().ingest_max_body_bytes + 1)
    response = client.post("/embeddings/ingest", json={"source_path": "x", "document_type": "t", "content": padding})
    assert response.status_code == 413
