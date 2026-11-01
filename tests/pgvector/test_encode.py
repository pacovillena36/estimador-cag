"""Tests unitarios (sin red ni BD) de POST /embeddings/encode."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import get_settings
from app.db.models import EMBEDDING_DIM
from app.dependencies import get_pipeline_embedder
from app.embedding_pipeline.embedder import MAX_INPUT_TOKENS
from app.embedding_pipeline.schemas import EncodeRequest
from app.main import app
from tests.pgvector.conftest import fake_vector


@pytest.fixture
def client(fake_pipeline_embedder):
    app.dependency_overrides[get_pipeline_embedder] = lambda: fake_pipeline_embedder
    # Sin `with`: no se ejecuta el lifespan (ni claves ni BD reales).
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.mark.parametrize("texts", [[], [""], ["ok", "   "]])
def test_encode_rejects_empty_list_or_texts(texts):
    with pytest.raises(ValidationError):
        EncodeRequest(texts=texts)


def test_encode_rejects_too_many_texts():
    with pytest.raises(ValidationError):
        EncodeRequest(texts=["a"] * (get_settings().encode_max_texts + 1))


def test_encode_returns_one_vector_per_text_in_order(client):
    texts = ["OAuth backend for banking", "  grocery delivery app  "]

    response = client.post("/embeddings/encode", json={"texts": texts})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"model", "embedding_dimension", "embeddings", "total_tokens", "encode_time_ms"}
    assert body["model"] == get_settings().embedding_model
    assert body["embedding_dimension"] == EMBEDDING_DIM
    # Los textos se recortan antes de embeber.
    assert body["embeddings"] == [fake_vector(text.strip()) for text in texts]
    assert body["total_tokens"] > 0


def test_encode_rejects_text_over_token_limit_without_calling_api(client, fake_pipeline_embedder):
    too_long = "word " * (MAX_INPUT_TOKENS + 1)

    response = client.post("/embeddings/encode", json={"texts": ["ok", too_long]})

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["type"] == "text_too_long"
    assert error["loc"] == ["body", "texts", 1]
    assert fake_pipeline_embedder.calls == 0


def test_encode_provider_failure_is_generic_502(client, fake_pipeline_embedder):
    fake_pipeline_embedder.failing = True

    response = client.post("/embeddings/encode", json={"texts": ["oauth"]})

    assert response.status_code == 502
    assert response.json()["error"]["message"] == "Embedding provider error"
    assert "APITimeoutError" not in response.text


def test_encode_rejects_oversized_body(client, fake_pipeline_embedder):
    padding = "a" * (get_settings().ingest_max_body_bytes + 1)

    response = client.post("/embeddings/encode", json={"texts": [padding]})

    assert response.status_code == 413
    assert fake_pipeline_embedder.calls == 0
