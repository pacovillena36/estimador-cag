"""Implementación nula del caché (SEMANTIC_CACHE_MODE=off): nunca hay hit y
no se escribe nada."""

from app.semantic_cache.ports import CachedEntry


class NoopEstimationCache:
    def lookup(self, *, bucket: str, vector: list[float]) -> CachedEntry | None:
        return None

    def store(self, *, bucket: str, vector: list[float], response: str) -> None:
        return None
