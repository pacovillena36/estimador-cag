"""Cinco consultas de ejemplo contra POST /search (top-5 de cada una).

Requiere la API levantada (docker compose up -d) y el corpus ingestado
(scripts/ingest_corpus.py). Uso:

    docker compose run --rm api python query_examples.py
    docker compose run --rm api python query_examples.py > output_examples.txt

SEARCH_API_URL fija la URL base (por defecto http://api:8000, el servicio
dentro de la red de docker-compose).
"""

import os
import sys

import httpx

BASE_URL = os.environ.get("SEARCH_API_URL", "http://api:8000").rstrip("/")
TIMEOUT_SECONDS = 30.0
TOP_K = 5
CONTENT_CHARS = 120

QUERIES = [
    ("Componente directo conocido", "REST API development with JWT authentication for financial sector"),
    (
        "Reformulación semántica",
        "secure backend service with token-based access control for banking applications",
    ),
    ("Dominio distinto", "mobile application for restaurant reservations"),
    ("Consulta ambigua", "integration with external system"),
    (
        "Consulta muy específica",
        "migration from monolith to microservices architecture using Kubernetes",
    ),
]


def search(client: httpx.Client, query: str) -> dict:
    response = client.post(f"{BASE_URL}/search", json={"query": query, "k": TOP_K})
    response.raise_for_status()
    return response.json()


def print_results(number: int, category: str, body: dict) -> None:
    print(f"=== [{number}] {category}")
    print(f"    Query: {body['query']}")
    print(f"    search_time_ms: {body['search_time_ms']}")
    print(f"    {'#':>2}  {'chunk_id':>8}  {'distance':>8}  {'chunk_type':<18}  content")
    for rank, result in enumerate(body["results"], start=1):
        content = " ".join(result["content"].split())[:CONTENT_CHARS]
        print(
            f"    {rank:>2}  {result['chunk_id']:>8}  {result['distance']:>8.4f}"
            f"  {result['chunk_type']:<18}  {content}"
        )
    print()


def main() -> int:
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for number, (category, query) in enumerate(QUERIES, start=1):
            try:
                body = search(client, query)
            except httpx.HTTPStatusError as exc:
                print(
                    f"Error: POST /search respondió {exc.response.status_code} para la query {number}.",
                    file=sys.stderr,
                )
                return 1
            except httpx.HTTPError as exc:
                print(f"Error: no se pudo llamar a {BASE_URL}/search ({type(exc).__name__}).", file=sys.stderr)
                return 1
            print_results(number, category, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
