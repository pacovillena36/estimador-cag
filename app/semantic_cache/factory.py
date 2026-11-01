"""Construcción del caché semántico según la configuración."""

from functools import lru_cache

from app.config import get_settings
from app.semantic_cache.noop import NoopEstimationCache
from app.semantic_cache.ports import CacheMode, EstimationCache
from app.semantic_cache.redis_cache import RedisSemanticEstimationCache


class SemanticCacheNotConfiguredError(RuntimeError):
    """SEMANTIC_CACHE_MODE != off sin REDIS_URL (la API no arranca)."""


@lru_cache
def get_semantic_cache() -> EstimationCache:
    """Instancia única, inyectada con Depends (sustituible en tests). No
    conecta con Redis al crearse: el índice se crea en el arranque."""
    settings = get_settings()
    if settings.semantic_cache_mode is CacheMode.OFF:
        return NoopEstimationCache()
    if settings.redis_url is None or not settings.redis_url.get_secret_value():
        raise SemanticCacheNotConfiguredError(
            "SEMANTIC_CACHE_MODE es 'shadow' o 'active' pero falta REDIS_URL "
            "(o pon SEMANTIC_CACHE_MODE=off)."
        )
    return RedisSemanticEstimationCache(
        settings.redis_url,
        index_name=settings.semantic_cache_index_name,
        distance_threshold=settings.semantic_cache_distance_threshold,
        ttl_seconds=settings.semantic_cache_ttl_seconds,
        socket_timeout_ms=settings.redis_socket_timeout_ms,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
    )
