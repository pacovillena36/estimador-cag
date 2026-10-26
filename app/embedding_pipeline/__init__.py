"""Pipeline de embeddings de presupuestos históricos (Sesiones 07 y 08).

presupuesto JSON -> JSONStructuralChunker (un componente = un chunk)
-> OpenAIEmbedder (text-embedding-3-small, async, en lote) -> Postgres +
pgvector (POST /embeddings/ingest) y búsqueda por distancia coseno
(POST /search).
"""
