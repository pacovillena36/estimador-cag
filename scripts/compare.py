"""Similitud coseno entre los embeddings de dos textos (sanity check).

Uso (desde la raíz del proyecto, con el .env cargado):

    uv run python scripts/compare.py --text-a "..." --text-b "..."
    docker compose exec api python scripts/compare.py --text-a "..." --text-b "..."
"""

import argparse
import math
import sys
from pathlib import Path

# Ejecutado como `python scripts/compare.py`, la raíz del proyecto no está
# en sys.path y `app` no se podría importar.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embedding_pipeline.embedder import (  # noqa: E402
    EmbeddingsNotConfiguredError,
    get_pipeline_embedder,
)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Producto escalar dividido por el producto de las normas."""
    if len(a) != len(b):
        raise ValueError(f"Vectors have different dimensions: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        raise ValueError("Cosine similarity is undefined for a zero vector")
    return dot / (norm_a * norm_b)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--text-a", required=True)
    parser.add_argument("--text-b", required=True)
    args = parser.parse_args()

    try:
        embedder = get_pipeline_embedder()
    except EmbeddingsNotConfiguredError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    similarity = cosine_similarity(embedder.embed_one(args.text_a), embedder.embed_one(args.text_b))
    print(f"Text A: {args.text_a}")
    print(f"Text B: {args.text_b}")
    print(f"Cosine similarity: {similarity:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
