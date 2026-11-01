"""Session 09 pre-work: read-only client trace of the current RAG system.

Client only: HTTP calls to existing endpoints plus SELECTs inside a READ ONLY
transaction that is rolled back. It adds no behaviour to the service. Raw
responses go to docs/session-09/trace/ as timestamped JSON; request headers and
credentials are never written there.

Usage, from the repository root, with the stack up (docker compose up -d):

    uv run --no-sync --env-file .env python scripts/session09_trace.py

Environment:
    ESTIMATOR_BASE_URL   default http://127.0.0.1:8000
    POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB   required
    POSTGRES_HOST        default 127.0.0.1
    POSTGRES_PORT        default 5432
"""

import asyncio
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import tiktoken

EMBEDDING_MODEL = "text-embedding-3-small"
MODEL_MAX_INPUT_TOKENS = 8191
HTTP_TIMEOUT_SECONDS = 60.0
OUTPUT_DIR = Path("docs/session-09/trace")
AMBIGUOUS = Path("examples/transcripts/02_ambiguous.txt")
CLEAR = Path("examples/transcripts/01_clear.txt")
# Literal fragments of 02_ambiguous.txt (line breaks of the file become spaces).
KEY_PHRASES = {
    "phrase_sell_online": "lo ideal sería vender por internet, eso seguro. Que la gente entre, vea los productos y compre",
    "phrase_card_payment": "que la gente pueda pagar con tarjeta, claro. Eso es fundamental, que el pago sea fácil y seguro",
}

NEAREST_SQL = """
SELECT c.id AS chunk_id,
       c.document_id,
       c.chunk_type,
       c.content,
       c.metadata::text AS metadata,
       d.metadata::text AS document_metadata,
       c.embedding <=> $1::text::vector AS distance
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.embedding IS NOT NULL
ORDER BY distance
LIMIT $2
"""


def save(stamp: str, name: str, payload: Any) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{stamp}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {path.as_posix()}")
    return path


def post(client: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        response = client.post(path, json=body)
    except httpx.HTTPError as exc:
        print(f"ERROR POST {path}: {type(exc).__name__}", file=sys.stderr)
        return {"path": path, "status_code": None, "error": type(exc).__name__}
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    try:
        parsed: Any = response.json()
    except ValueError:
        parsed = response.text
    print(f"POST {path} -> HTTP {response.status_code} ({elapsed_ms} ms)")
    if response.status_code >= 400:
        print(json.dumps(parsed, ensure_ascii=False)[:600])
    return {"path": path, "status_code": response.status_code, "elapsed_ms": elapsed_ms, "body": parsed}


def vector_stats(vector: list[float]) -> dict[str, Any]:
    return {
        "dimensions": len(vector),
        "l2_norm": math.sqrt(sum(value * value for value in vector)),
        "first_component": vector[0],
        "last_component": vector[-1],
    }


def dispersion(distances: list[float]) -> dict[str, float | int]:
    return {
        "n": len(distances),
        "min_distance": min(distances),
        "max_distance": max(distances),
        "spread": max(distances) - min(distances),
        "mean_distance": statistics.fmean(distances),
        "pstdev": statistics.pstdev(distances),
    }


def budget_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        budget_id = row["metadata"]["budget_id"]
        counts[budget_id] = counts.get(budget_id, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def sector_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        sector = row["metadata"]["client_sector"]
        counts[sector] = counts.get(sector, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


async def nearest(conn: asyncpg.Connection, vector: list[float], k: int) -> list[dict[str, Any]]:
    records = await conn.fetch(NEAREST_SQL, json.dumps(vector), k)
    return [
        {
            "chunk_id": record["chunk_id"],
            "document_id": record["document_id"],
            "chunk_type": record["chunk_type"],
            "content": record["content"],
            "metadata": json.loads(record["metadata"]),
            "document_metadata": json.loads(record["document_metadata"]),
            "distance": record["distance"],
        }
        for record in records
    ]


def analyse(label: str, rows_top20: list[dict[str, Any]]) -> dict[str, Any]:
    top5 = rows_top20[:5]
    summary = {
        "label": label,
        "top5_dispersion": dispersion([row["distance"] for row in top5]),
        "top20_dispersion": dispersion([row["distance"] for row in rows_top20]),
        "top5_budgets": budget_distribution(top5),
        "top5_sectors": sector_distribution(top5),
        "top20_sectors": sector_distribution(rows_top20),
    }
    print(f"\n--- {label}")
    for rank, row in enumerate(top5, start=1):
        meta = row["metadata"]
        print(
            f"{rank} chunk={row['chunk_id']} d={row['distance']:.4f} {meta['budget_id']} "
            f"{meta['client_sector']} {meta['component_id']}"
        )
    print(json.dumps({key: summary[key] for key in ("top5_dispersion", "top20_dispersion", "top5_budgets", "top20_sectors")}))
    return summary


async def run(stamp: str, base_url: str, conn: asyncpg.Connection) -> int:
    encoding = tiktoken.encoding_for_model(EMBEDDING_MODEL)
    ambiguous = AMBIGUOUS.read_text(encoding="utf-8").strip()
    clear = CLEAR.read_text(encoding="utf-8").strip()

    read_only = await conn.fetchval("SHOW transaction_read_only")
    print(f"transaction_read_only = {read_only}")

    with httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT_SECONDS) as client:
        # Step 1: input size against corpus and limits.
        contents = [record["content"] for record in await conn.fetch("SELECT content FROM chunks")]
        chunk_tokens = [len(encoding.encode(content)) for content in contents]
        chunk_chars = [len(content) for content in contents]
        oversized_probe = post(client, "/embeddings/encode", {"texts": ["word " * 9000]})
        step1 = {
            "model": EMBEDDING_MODEL,
            "model_max_input_tokens": MODEL_MAX_INPUT_TOKENS,
            "transcripts": {
                str(path): {"chars": len(text), "tokens": len(encoding.encode(text))}
                for path, text in ((AMBIGUOUS, ambiguous), (CLEAR, clear))
            },
            "corpus_chunks": {
                "n": len(contents),
                "mean_chars": statistics.fmean(chunk_chars),
                "min_chars": min(chunk_chars),
                "max_chars": max(chunk_chars),
                "mean_tokens": statistics.fmean(chunk_tokens),
                "min_tokens": min(chunk_tokens),
                "max_tokens": max(chunk_tokens),
            },
            # Synthetic text, no client data: what happens above the model limit.
            "oversized_encode_probe": {
                "tokens": len(encoding.encode("word " * 9000)),
                "status_code": oversized_probe["status_code"],
                "body": oversized_probe.get("body"),
            },
        }
        print(json.dumps(step1, ensure_ascii=False, indent=2))
        save(stamp, "step1_input_measures", step1)

        # Step 2: embed the whole ambiguous transcript.
        encoded = post(client, "/embeddings/encode", {"texts": [ambiguous, clear, *KEY_PHRASES.values()]})
        if encoded["status_code"] != 200:
            save(stamp, "step2_encode_error", encoded)
            return 1
        vectors = encoded["body"]["embeddings"]
        vector_by_label = dict(zip(["ambiguous", "clear", *KEY_PHRASES], vectors, strict=True))
        step2 = {
            "model": encoded["body"]["model"],
            "embedding_dimension": encoded["body"]["embedding_dimension"],
            "total_tokens_all_inputs": encoded["body"]["total_tokens"],
            "encode_time_ms": encoded["body"]["encode_time_ms"],
            "stats": {label: vector_stats(vector) for label, vector in vector_by_label.items()},
            "vectors": vector_by_label,
        }
        print(json.dumps(step2["stats"], indent=2))
        save(stamp, "step2_encode", step2)

        # Step 3: /search with the whole transcript, then the same ranking by SQL.
        search_ambiguous = post(client, "/search", {"query": ambiguous, "k": 5})
        save(stamp, "step3_search_ambiguous_full", search_ambiguous)
        rows_by_label = {label: await nearest(conn, vector, 20) for label, vector in vector_by_label.items()}
        save(stamp, "step3_sql_top5_ambiguous", rows_by_label["ambiguous"][:5])

        # Step 5: contrast measures.
        search_clear = post(client, "/search", {"query": clear, "k": 5})
        save(stamp, "step5_search_clear", search_clear)
        phrase_searches = {
            label: post(client, "/search", {"query": phrase, "k": 5}) for label, phrase in KEY_PHRASES.items()
        }
        save(stamp, "step5_search_phrases", phrase_searches)

        # Does SQL reproduce /search? Only checkable where /search accepted the query.
        checks = {}
        for label, response in (("clear", search_clear), *phrase_searches.items()):
            if response["status_code"] == 200:
                api_hits = [(hit["chunk_id"], hit["distance"]) for hit in response["body"]["results"]]
                sql_hits = [(row["chunk_id"], row["distance"]) for row in rows_by_label[label][:5]]
                checks[label] = {
                    "same_chunk_order": [hit[0] for hit in api_hits] == [hit[0] for hit in sql_hits],
                    "max_abs_distance_diff": max(abs(a[1] - b[1]) for a, b in zip(api_hits, sql_hits, strict=True)),
                }
        print("sql_vs_search:", json.dumps(checks))

        step5 = {
            "summaries": {label: analyse(label, rows) for label, rows in rows_by_label.items()},
            "sql_vs_search": checks,
            "top20": {
                label: [
                    {key: row[key] for key in ("chunk_id", "distance")}
                    | {"budget_id": row["metadata"]["budget_id"], "sector": row["metadata"]["client_sector"]}
                    for row in rows
                ]
                for label, rows in rows_by_label.items()
            },
        }
        save(stamp, "step5_contrast", step5)
    return 0


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    base_url = os.environ.get("ESTIMATOR_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    missing = [name for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB") if not os.environ.get(name)]
    if missing:
        print(f"ERROR: missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conn = await asyncpg.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
        timeout=10,
    )
    transaction = conn.transaction(readonly=True)
    await transaction.start()
    try:
        return await run(stamp, base_url, conn)
    finally:
        await transaction.rollback()
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
