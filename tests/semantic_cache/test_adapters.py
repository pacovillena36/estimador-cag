"""Adaptadores sin servicios externos: Redis inalcanzable (fail-open con
cooldown) y cliente de embeddings con el HTTP simulado."""

import time

import httpx
import pytest
from pydantic import SecretStr

from app.embeddings import EmbeddingError, OpenAIEmbedder
from app.semantic_cache.ports import CacheUnavailableError
from app.semantic_cache.redis_cache import RedisSemanticEstimationCache


def unreachable_cache(**kwargs) -> RedisSemanticEstimationCache:
    # Puerto 1 en localhost: conexión rechazada al instante.
    return RedisSemanticEstimationCache(
        SecretStr("redis://127.0.0.1:1/0"),
        index_name="estimation_cache",
        distance_threshold=0.08,
        ttl_seconds=3600,
        socket_timeout_ms=150,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=8,
        **kwargs,
    )


def test_unreachable_redis_raises_cache_unavailable():
    cache = unreachable_cache()
    with pytest.raises(CacheUnavailableError):
        cache.lookup(bucket="b", vector=[0.1] * 8)
    with pytest.raises(CacheUnavailableError):
        cache.store(bucket="b", vector=[0.1] * 8, response="{}")


def test_after_a_failure_it_waits_before_retrying_the_connection():
    cache = unreachable_cache(retry_init_after_s=60)
    with pytest.raises(CacheUnavailableError):
        cache.lookup(bucket="b", vector=[0.1] * 8)

    start = time.perf_counter()
    with pytest.raises(CacheUnavailableError, match="init_cooldown"):
        cache.lookup(bucket="b", vector=[0.1] * 8)
    assert time.perf_counter() - start < 0.05  # no vuelve a intentar conectar


def make_embedder(handler, dimensions=4) -> tuple[OpenAIEmbedder, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    embedder = OpenAIEmbedder(SecretStr("sk-test"), model="text-embedding-3-small", dimensions=dimensions, timeout_ms=1000)
    embedder._client = embedder._client.with_options(http_client=httpx.Client(transport=httpx.MockTransport(record)))
    return embedder, requests


def _embedding_response(vector):
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": vector}],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


def test_embedder_requests_the_configured_dimensions():
    embedder, requests = make_embedder(lambda _: httpx.Response(200, json=_embedding_response([0.1, 0.2, 0.3, 0.4])))
    assert embedder.embed("texto") == [0.1, 0.2, 0.3, 0.4]
    (request,) = requests
    assert b'"dimensions":4' in request.content.replace(b" ", b"")


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)),
        lambda _: httpx.Response(500, json={"error": {"message": "boom"}}),
        lambda _: httpx.Response(200, json=_embedding_response([0.1, 0.2])),  # otra dimensión
    ],
    ids=["timeout", "http_500", "dimension_mismatch"],
)
def test_embedder_failures_raise_embedding_error_without_retries(handler):
    embedder, requests = make_embedder(handler)
    with pytest.raises(EmbeddingError):
        embedder.embed("texto")
    assert len(requests) == 1
