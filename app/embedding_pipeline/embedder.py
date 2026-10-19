"""Embedder del pipeline: text-embedding-3-small de OpenAI, en batches.

A diferencia del embedder del caché semántico (app/embeddings.py, una
llamada por petición con timeout corto y sin reintentos), aquí se embeben
lotes de chunks fuera del camino crítico: timeout amplio, batches de hasta
BATCH_SIZE entradas y reintento con espera exponencial ante RateLimitError.
El resto de errores de la API se propagan.

Se usa la dimensión por defecto del modelo (1536): no se pide `dimensions`.
"""

import time
from collections.abc import Sequence
from functools import lru_cache

import openai
import structlog
from pydantic import SecretStr

from app.config import get_settings
from app.embedding_pipeline.schemas import Chunk, EmbeddedChunk

log = structlog.get_logger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"

# Precio de text-embedding-3-small en USD por millón de tokens de entrada
# (octubre de 2026). Cambia con el tiempo: revisar en la página de precios
# de OpenAI.
PRICE_USD_PER_MILLION_TOKENS = 0.02

# Límites de la API: máximo de tokens por entrada y total por petición.
MAX_INPUT_TOKENS = 8191
MAX_TOKENS_PER_REQUEST = 300_000

BATCH_SIZE = 100
RATE_LIMIT_BACKOFF_SECONDS = (1, 2, 4)
TIMEOUT_SECONDS = 30.0


class EmbeddingsNotConfiguredError(RuntimeError):
    """No hay EMBEDDING_API_KEY ni OPENAI_API_KEY."""


def estimate_cost_usd(tokens: int) -> float:
    return tokens * PRICE_USD_PER_MILLION_TOKENS / 1_000_000


class OpenAIEmbedder:
    def __init__(self, api_key: SecretStr, *, model: str = EMBEDDING_MODEL) -> None:
        self._model = model
        # max_retries=0: el único reintento es el de RateLimitError de abajo.
        self._client = openai.OpenAI(
            api_key=api_key.get_secret_value(),
            timeout=TIMEOUT_SECONDS,
            max_retries=0,
        )

    def embed_one(self, text: str) -> list[float]:
        return self._create([text])[0]

    def embed_many(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        embedded = []
        batches = list(self._batches(chunks))
        for number, batch in enumerate(batches, start=1):
            start = time.perf_counter()
            vectors = self._create([chunk.text for chunk in batch])
            log.info(
                "embeddings.batch_embedded",
                batch=number,
                batches=len(batches),
                chunks=len(batch),
                tokens=sum(chunk.token_count for chunk in batch),
                latency_ms=round((time.perf_counter() - start) * 1000, 1),
            )
            embedded.extend(
                EmbeddedChunk(**chunk.model_dump(), embedding=vector)
                for chunk, vector in zip(batch, vectors, strict=True)
            )
        return embedded

    @staticmethod
    def _batches(chunks: list[Chunk]):
        """Lotes de hasta BATCH_SIZE chunks sin pasar de MAX_TOKENS_PER_REQUEST."""
        batch: list[Chunk] = []
        tokens = 0
        for chunk in chunks:
            if batch and (len(batch) == BATCH_SIZE or tokens + chunk.token_count > MAX_TOKENS_PER_REQUEST):
                yield batch
                batch, tokens = [], 0
            batch.append(chunk)
            tokens += chunk.token_count
        if batch:
            yield batch

    def _create(self, texts: Sequence[str]) -> list[list[float]]:
        for attempt, wait in enumerate((*RATE_LIMIT_BACKOFF_SECONDS, None), start=1):
            try:
                response = self._client.embeddings.create(model=self._model, input=list(texts))
            except openai.RateLimitError:
                if wait is None:
                    raise
                log.warning("embeddings.rate_limited", attempt=attempt, retry_in_s=wait)
                time.sleep(wait)
                continue
            # La API devuelve un índice por entrada: se ordena por él.
            return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        raise AssertionError("unreachable")


@lru_cache
def get_pipeline_embedder() -> OpenAIEmbedder:
    """Instancia única, inyectada con Depends (sustituible en tests).

    Misma clave que el caché semántico: EMBEDDING_API_KEY (permisos
    mínimos) o, si no se define, OPENAI_API_KEY.
    """
    settings = get_settings()
    api_key = settings.embedding_api_key or settings.openai_api_key
    if api_key is None or not api_key.get_secret_value():
        raise EmbeddingsNotConfiguredError(
            "El pipeline de embeddings necesita EMBEDDING_API_KEY u OPENAI_API_KEY."
        )
    return OpenAIEmbedder(api_key)
