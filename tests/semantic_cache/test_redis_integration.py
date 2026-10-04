"""Adaptador real contra Redis Stack (búsqueda vectorial) con testcontainers.

fakeredis no soporta búsqueda vectorial, así que estos tests levantan un
contenedor. Se saltan si Docker no está disponible. Los vectores son
sintéticos (sin API de embeddings): una "reformulación" es un vector muy
cercano y una descripción distinta, uno casi ortogonal.
"""

import math
import uuid

import pytest
from pydantic import SecretStr

from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType
from app.semantic_cache.bucket import build_bucket_key
from app.semantic_cache.redis_cache import RedisSemanticEstimationCache

pytestmark = pytest.mark.integration

IMAGE = "redis/redis-stack-server:7.4.0-v6"
DIMS = 8
TTL_SECONDS = 600

REQUEST = EstimationRequest(
    description="Portal interno para reservar salas y puestos de trabajo.",
    project_type=ProjectType.INTERNAL_TOOL,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)
ORIGINAL = [1.0, 0.8, 0.1, 0.0, 0.3, 0.0, 0.5, 0.2]
REPHRASED = [1.0, 0.82, 0.12, 0.0, 0.29, 0.01, 0.5, 0.2]  # distancia coseno ~0.0005
UNRELATED = [0.0, 0.0, 0.9, 1.0, 0.0, 0.7, 0.0, 0.0]


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return 1 - dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@pytest.fixture(scope="module")
def redis_url():
    try:
        from testcontainers.redis import RedisContainer

        container = RedisContainer(IMAGE)
        container.start()
    except Exception as exc:  # Docker no disponible en esta máquina/CI
        pytest.skip(f"Docker/Redis Stack no disponible: {type(exc).__name__}")
    host, port = container.get_container_host_ip(), container.get_exposed_port(6379)
    yield f"redis://{host}:{port}"
    container.stop()


@pytest.fixture
def cache(redis_url) -> RedisSemanticEstimationCache:
    cache = RedisSemanticEstimationCache(
        SecretStr(redis_url),
        index_name=f"test_{uuid.uuid4().hex[:8]}",  # índice aislado por test
        distance_threshold=0.08,
        ttl_seconds=TTL_SECONDS,
        socket_timeout_ms=2000,
        embedding_model="test-embeddings",
        embedding_dimensions=DIMS,
    )
    cache.ensure_index()
    return cache


def bucket(request=REQUEST, prompt_version="v3", tenant_id="tenant-a") -> str:
    return build_bucket_key(request, prompt_version=prompt_version, tenant_id=tenant_id)


def test_rephrased_description_in_the_same_bucket_is_a_hit(cache):
    assert cosine_distance(ORIGINAL, REPHRASED) < 0.08 < cosine_distance(ORIGINAL, UNRELATED)
    cache.store(bucket=bucket(), vector=ORIGINAL, response='{"cached": "json"}')

    hit = cache.lookup(bucket=bucket(), vector=REPHRASED)

    assert hit is not None
    assert hit.response == '{"cached": "json"}'
    assert hit.distance == pytest.approx(cosine_distance(ORIGINAL, REPHRASED), abs=1e-3)
    assert cache.lookup(bucket=bucket(), vector=UNRELATED) is None


@pytest.mark.parametrize(
    "other_bucket",
    [
        lambda: bucket(REQUEST.model_copy(update={"output_format": OutputFormat.NARRATIVE})),
        lambda: bucket(tenant_id="tenant-b"),
        lambda: bucket(prompt_version="v4"),
    ],
    ids=["output_format", "tenant", "prompt_version"],
)
def test_same_description_in_another_bucket_is_a_miss(cache, other_bucket):
    cache.store(bucket=bucket(), vector=ORIGINAL, response="{}")
    assert cache.lookup(bucket=other_bucket(), vector=ORIGINAL) is None


def test_entries_have_ttl_and_no_user_text(cache, redis_url):
    from redis import Redis

    cache.store(bucket=bucket(), vector=ORIGINAL, response="{}")
    client = Redis.from_url(redis_url, decode_responses=False)
    (key,) = client.keys(f"{cache._index_name}:*")

    assert 0 < client.ttl(key) <= TTL_SECONDS
    stored_prompt = client.hget(key, "prompt").decode()
    assert len(stored_prompt) == 64  # hash del vector, no texto del usuario


def test_index_creation_is_idempotent_and_keeps_data(cache, redis_url):
    cache.store(bucket=bucket(), vector=ORIGINAL, response="{}")
    again = RedisSemanticEstimationCache(
        SecretStr(redis_url),
        index_name=cache._index_name,
        distance_threshold=0.08,
        ttl_seconds=TTL_SECONDS,
        socket_timeout_ms=2000,
        embedding_model="test-embeddings",
        embedding_dimensions=DIMS,
    )
    again.ensure_index()
    assert again.lookup(bucket=bucket(), vector=ORIGINAL) is not None
