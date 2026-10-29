"""Embedder del pipeline: text-embedding-3-small de OpenAI, async y en lotes.

A diferencia del embedder del caché semántico (app/embeddings.py, una
llamada por petición con timeout corto y sin reintentos), aquí se embeben
todos los chunks de un documento en una sola llamada (sub-lotes solo si se
superan los límites de la API) con timeout amplio y reintentos acotados del
SDK (429, 5xx, timeouts). Se usa la dimensión por defecto del modelo: no se
pide `dimensions`, pero se comprueba la de cada vector devuelto.

Cualquier fallo (red, API, respuesta incoherente) sale como
EmbeddingProviderError; la ingesta y la búsqueda lo traducen a 502.
"""

import time
from collections.abc import Iterator, Sequence
from functools import lru_cache
from typing import Protocol

import openai
import structlog
from pydantic import SecretStr

from app.config import get_settings
from app.embedding_pipeline.schemas import Chunk

log = structlog.get_logger(__name__)

# Precio de text-embedding-3-small en USD por millón de tokens de entrada
# (octubre de 2026). Cambia con el tiempo: revisar en la página de precios
# de OpenAI.
PRICE_USD_PER_MILLION_TOKENS = 0.02

# Límites de la API: tokens por entrada, entradas y tokens por petición.
MAX_INPUT_TOKENS = 8191
MAX_INPUTS_PER_REQUEST = 2048
MAX_TOKENS_PER_REQUEST = 300_000


class EmbeddingsNotConfiguredError(RuntimeError):
    """No hay EMBEDDING_API_KEY ni OPENAI_API_KEY."""


class EmbeddingProviderError(Exception):
    """La API de embeddings falló o devolvió una respuesta incoherente."""


def estimate_cost_usd(tokens: int) -> float:
    return tokens * PRICE_USD_PER_MILLION_TOKENS / 1_000_000


class PipelineEmbedder(Protocol):
    async def embed_chunks(self, chunks: Sequence[Chunk]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str,
        dimensions: int,
        timeout_s: float,
        max_retries: int,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._client = openai.AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            timeout=timeout_s,
            max_retries=max_retries,
        )

    async def embed_query(self, text: str) -> list[float]:
        return (await self._create([text]))[0]

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Un vector por texto, en el mismo orden, en una sola llamada (quien
        llama acota el número de textos y sus tokens a los límites de la API)."""
        return await self._create(list(texts))

    async def embed_chunks(self, chunks: Sequence[Chunk]) -> list[list[float]]:
        """Un vector por chunk, en el mismo orden."""
        vectors: list[list[float]] = []
        batches = list(_batches(chunks))
        for number, batch in enumerate(batches, start=1):
            start = time.perf_counter()
            vectors.extend(await self._create([chunk.text for chunk in batch]))
            log.info(
                "embeddings.batch_embedded",
                batch=number,
                batches=len(batches),
                chunks=len(batch),
                tokens=sum(chunk.token_count for chunk in batch),
                latency_ms=round((time.perf_counter() - start) * 1000, 1),
            )
        return vectors

    async def _create(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.embeddings.create(model=self._model, input=texts)
        except openai.OpenAIError as exc:  # timeout, conexión, rate limit, 4xx/5xx
            raise EmbeddingProviderError(type(exc).__name__) from exc
        # La API devuelve un índice por entrada: no se asume el orden.
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts):
            raise EmbeddingProviderError(f"count_mismatch:{len(vectors)}!={len(texts)}")
        if any(len(vector) != self._dimensions for vector in vectors):
            raise EmbeddingProviderError("dimension_mismatch")
        return vectors


def _batches(chunks: Sequence[Chunk]) -> Iterator[list[Chunk]]:
    """Lotes de hasta MAX_INPUTS_PER_REQUEST chunks sin pasar de
    MAX_TOKENS_PER_REQUEST (un documento normal cabe en uno solo)."""
    batch: list[Chunk] = []
    tokens = 0
    for chunk in chunks:
        if batch and (
            len(batch) == MAX_INPUTS_PER_REQUEST or tokens + chunk.token_count > MAX_TOKENS_PER_REQUEST
        ):
            yield batch
            batch, tokens = [], 0
        batch.append(chunk)
        tokens += chunk.token_count
    if batch:
        yield batch


@lru_cache
def get_pipeline_embedder() -> PipelineEmbedder:
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
    return OpenAIEmbedder(
        api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dim,
        timeout_s=settings.pipeline_embedding_timeout_s,
        max_retries=settings.pipeline_embedding_max_retries,
    )
