"""Ingesta el corpus de presupuestos llamando a POST /embeddings/ingest.

Recorre los JSON del directorio del corpus (por defecto data/). Un fichero
con una lista de presupuestos se ingesta como un documento por presupuesto
(source_path = "<fichero>#<budget_id>"); un fichero con un solo
presupuesto, como un documento. Idempotente: un 409 es "ya ingestado".

`docker compose up` lo ejecuta solo (servicio "seed", con la API healthy);
para relanzarlo a mano:

    docker compose run --rm seed
    docker compose run --rm api python scripts/ingest_corpus.py [CORPUS_DIR]

INGEST_API_URL fija la URL base (por defecto http://api:8000).
"""

import json
import os
import sys
from pathlib import Path

import httpx

BASE_URL = os.environ.get("INGEST_API_URL", "http://api:8000").rstrip("/")
TIMEOUT_SECONDS = 60.0
DOCUMENT_TYPE = "historical_budget"


def documents(corpus_dir: Path):
    for path in sorted(corpus_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for budget in data:
                yield f"{path.as_posix()}#{budget['budget_id']}", budget
        else:
            yield path.as_posix(), data


def main() -> int:
    corpus_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    counts = {"ingested": 0, "already": 0, "failed": 0}
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for source_path, content in documents(corpus_dir):
            body = {"source_path": source_path, "document_type": DOCUMENT_TYPE, "content": content}
            try:
                response = client.post(f"{BASE_URL}/embeddings/ingest", json=body)
            except httpx.HTTPError as exc:
                print(f"ERROR    {source_path}: {type(exc).__name__}", file=sys.stderr)
                counts["failed"] += 1
                continue
            if response.status_code == 200:
                result = response.json()
                print(f"ingested {source_path} -> document {result['document_id']} ({result['chunks_created']} chunks)")
                counts["ingested"] += 1
            elif response.status_code == 409:
                print(f"skipped  {source_path} (ya ingestado: document {response.json()['document_id']})")
                counts["already"] += 1
            else:
                print(f"ERROR    {source_path}: HTTP {response.status_code}", file=sys.stderr)
                counts["failed"] += 1
    print(f"Total: {counts['ingested']} ingestados, {counts['already']} ya existentes, {counts['failed']} fallidos")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
