"""Cliente de embeddings para el caché semántico (OpenAI).

- Timeout corto (EMBEDDING_TIMEOUT_MS) y sin reintentos: el embedding se
  calcula en cada petición con el caché activo y no debe añadir más latencia
  de la que ahorra un hit. Si falla, el servicio sigue por el LLM.
- Se pide la dimensión explícita (EMBEDDING_DIMENSIONS) para que coincida
  siempre con la del índice de Redis, y se comprueba la respuesta.
- Clave separada y de permisos mínimos (EMBEDDING_API_KEY); si no se
  define, se usa OPENAI_API_KEY. Hace falta aunque el LLM sea Anthropic
  (Anthropic no ofrece API de embeddings).
"""

from functools import lru_cache
from typing import Protocol

import openai
from pydantic import SecretStr

from app.config import get_settings
from app.semantic_cache.ports import CacheMode


class EmbeddingError(Exception):
    """La API de embeddings falló, superó el timeout o devolvió otra dimensión."""


class EmbeddingsNotConfiguredError(RuntimeError):
    """Caché activo sin API key para embeddings (la API no arranca)."""


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    def __init__(self, api_key: SecretStr, *, model: str, dimensions: int, timeout_ms: int) -> None:
        self._model = model
        self._dimensions = dimensions
        self._client = openai.OpenAI(
            api_key=api_key.get_secret_value(),
            timeout=timeout_ms / 1000,
            max_retries=0,
        )

    def embed(self, text: str) -> list[float]:
        try:
            response = self._client.embeddings.create(
                model=self._model, input=text, dimensions=self._dimensions
            )
        except openai.APIError as exc:  # timeout, conexión y errores HTTP
            raise EmbeddingError(type(exc).__name__) from exc
        vector = response.data[0].embedding
        if len(vector) != self._dimensions:
            raise EmbeddingError(f"dimension_mismatch:{len(vector)}")
        return vector


class DisabledEmbedder:
    """Con el caché en modo off nunca se calculan embeddings."""

    def embed(self, text: str) -> list[float]:
        raise EmbeddingError("semantic_cache_off")


@lru_cache
def get_embedder() -> Embedder:
    """Instancia única, inyectada con Depends (sustituible en tests)."""
    settings = get_settings()
    if settings.semantic_cache_mode is CacheMode.OFF:
        return DisabledEmbedder()
    api_key = settings.embedding_api_key or settings.openai_api_key
    if api_key is None or not api_key.get_secret_value():
        raise EmbeddingsNotConfiguredError(
            "El caché semántico necesita EMBEDDING_API_KEY u OPENAI_API_KEY."
        )
    return OpenAIEmbedder(
        api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        timeout_ms=settings.embedding_timeout_ms,
    )
