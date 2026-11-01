"""Adaptador del caché semántico sobre Redis Stack + redisvl (SemanticCache).

Notas sobre redisvl 0.27 (verificado en el código instalado):
- Import: `from redisvl.extensions.cache.llm import SemanticCache`.
- `check(vector=..., filter_expression=Tag("bucket") == ...)` y
  `store(..., vector=..., filters={"bucket": ...})`; el campo `bucket` se
  declara en `filterable_fields` al crear el índice.
- Si no se le pasa un vectorizador, SemanticCache descarga un modelo de
  HuggingFace; y CustomVectorizer llama a embed() al construirse. Como aquí
  los vectores siempre se calculan fuera (una vez por petición, en el
  servicio), se usa un vectorizador que solo declara la dimensión.
- Un hit refresca el TTL de la entrada (TTL deslizante): una entrada que se
  consulta a menudo vive más que SEMANTIC_CACHE_TTL_SECONDS desde su
  creación. Las de versiones de prompt antiguas dejan de consultarse y
  caducan.

No se persiste texto libre del usuario: el campo `prompt` de redisvl
guarda un hash del vector, no la descripción.

Fail-open: cualquier fallo se traduce a CacheUnavailableError (el servicio
lo registra y sigue por el LLM). Si Redis no está disponible al crear el
índice, se reintenta como mucho cada `retry_init_after_s` para no sumar el
timeout de conexión a cada petición mientras Redis está caído.
"""

import hashlib
import threading
import time
from typing import Any

from pydantic import SecretStr
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from redisvl.exceptions import RedisVLError
from redisvl.extensions.cache.llm import SemanticCache
from redisvl.query.filter import Tag
from redisvl.utils.vectorize import BaseVectorizer

from app.semantic_cache.ports import CachedEntry, CacheUnavailableError

BUCKET_FIELD = "bucket"

# redisvl envuelve muchos errores de conexión en sus propias excepciones
# (p. ej. RedisSearchError al consultar FT.INFO): todos cuentan como caché
# no disponible.
_CACHE_ERRORS = (RedisError, RedisVLError, OSError)


class PrecomputedVectorizer(BaseVectorizer):
    """Solo declara modelo y dimensión del índice; nunca calcula vectores."""

    def _embed(self, *args: Any, **kwargs: Any) -> list[float]:
        raise NotImplementedError("Los vectores se calculan fuera y se pasan con vector=")


class RedisSemanticEstimationCache:
    def __init__(
        self,
        redis_url: SecretStr,
        *,
        index_name: str,
        distance_threshold: float,
        ttl_seconds: int,
        socket_timeout_ms: int,
        embedding_model: str,
        embedding_dimensions: int,
        retry_init_after_s: float = 30.0,
    ) -> None:
        self._redis_url = redis_url
        self._index_name = index_name
        self._distance_threshold = distance_threshold
        self._ttl_seconds = ttl_seconds
        self._timeout_s = socket_timeout_ms / 1000
        self._vectorizer = PrecomputedVectorizer(model=embedding_model, dims=embedding_dimensions)
        self._retry_init_after_s = retry_init_after_s
        self._cache: SemanticCache | None = None
        self._last_init_failure: float | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- índice

    def ensure_index(self) -> None:
        """Crea el índice si no existe (idempotente, no borra datos). Se
        llama en el arranque y, de forma perezosa, en la primera petición."""
        self._get_cache(force=True)

    def _get_cache(self, *, force: bool = False) -> SemanticCache:
        if self._cache is not None:
            return self._cache
        with self._lock:
            if self._cache is not None:
                return self._cache
            if (
                not force
                and self._last_init_failure is not None
                and time.monotonic() - self._last_init_failure < self._retry_init_after_s
            ):
                raise CacheUnavailableError("init_cooldown")
            try:
                client = Redis.from_url(
                    self._redis_url.get_secret_value(),
                    socket_timeout=self._timeout_s,
                    socket_connect_timeout=self._timeout_s,
                    # Sin reintentos: el caché no debe añadir más latencia
                    # de la que ahorra (redis-py reintenta 3 veces por defecto).
                    retry=Retry(NoBackoff(), 0),
                )
                self._cache = SemanticCache(
                    name=self._index_name,
                    redis_client=client,
                    vectorizer=self._vectorizer,
                    distance_threshold=self._distance_threshold,
                    ttl=self._ttl_seconds,
                    filterable_fields=[{"name": BUCKET_FIELD, "type": "tag"}],
                    overwrite=False,
                )
            except (*_CACHE_ERRORS, ValueError) as exc:
                # ValueError: el índice existe con otro schema (p. ej. otra
                # dimensión de embeddings). Nunca se sobrescribe solo.
                self._last_init_failure = time.monotonic()
                raise CacheUnavailableError(type(exc).__name__) from exc
            self._last_init_failure = None
            return self._cache

    # ------------------------------------------------------------- puerto

    def lookup(self, *, bucket: str, vector: list[float]) -> CachedEntry | None:
        cache = self._get_cache()
        try:
            hits = cache.check(
                vector=vector,
                filter_expression=Tag(BUCKET_FIELD) == bucket,
                num_results=1,
                return_fields=["response", "vector_distance"],
            )
        except _CACHE_ERRORS as exc:
            raise CacheUnavailableError(type(exc).__name__) from exc
        if not hits:
            return None
        hit = hits[0]
        return CachedEntry(response=hit["response"], distance=float(hit["vector_distance"]))

    def store(self, *, bucket: str, vector: list[float], response: str) -> None:
        cache = self._get_cache()
        try:
            cache.store(
                # redisvl exige un `prompt`: se guarda un hash del vector, no
                # el texto del usuario (el vector ya se pasa explícito).
                prompt=hashlib.sha256(repr(vector).encode("utf-8")).hexdigest(),
                response=response,
                vector=vector,
                filters={BUCKET_FIELD: bucket},
            )
        except _CACHE_ERRORS as exc:
            raise CacheUnavailableError(type(exc).__name__) from exc
