"""Encode de textos a embeddings, sin persistir nada.

Mismo modelo y dimensión que la ingesta y la búsqueda, así que los vectores
devueltos son comparables con los de chunks.embedding. Los textos se
tokenizan antes de llamar a la API: uno de más de MAX_INPUT_TOKENS es un
error del cliente (422), no un fallo del proveedor (502).
"""

import time
from dataclasses import dataclass

import structlog
import tiktoken

from app.embedding_pipeline.embedder import MAX_INPUT_TOKENS, PipelineEmbedder, estimate_cost_usd

log = structlog.get_logger(__name__)


class TextTooLongError(Exception):
    def __init__(self, indices: list[int]) -> None:
        super().__init__(f"texts exceed {MAX_INPUT_TOKENS} tokens: {indices}")
        self.indices = indices


@dataclass(frozen=True)
class EncodeOutcome:
    embeddings: list[list[float]]
    total_tokens: int
    encode_time_ms: int


class EncodingService:
    def __init__(self, embedder: PipelineEmbedder, *, model: str) -> None:
        self._embedder = embedder
        # Mismo tokenizador que el modelo de embeddings.
        self._encoding = tiktoken.encoding_for_model(model)

    async def encode(self, texts: list[str]) -> EncodeOutcome:
        t0 = time.perf_counter()
        token_counts = [len(self._encoding.encode(text)) for text in texts]
        too_long = [i for i, tokens in enumerate(token_counts) if tokens > MAX_INPUT_TOKENS]
        if too_long:
            raise TextTooLongError(too_long)

        embeddings = await self._embedder.embed_texts(texts)
        encode_time_ms = int((time.perf_counter() - t0) * 1000)
        total_tokens = sum(token_counts)
        # Sin los textos (pueden contener datos del cliente).
        log.info(
            "embeddings.encoded",
            texts=len(texts),
            tokens=total_tokens,
            estimated_cost_usd=round(estimate_cost_usd(total_tokens), 8),
            encode_time_ms=encode_time_ms,
        )
        return EncodeOutcome(embeddings, total_tokens, encode_time_ms)
