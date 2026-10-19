"""Pipeline de embeddings de presupuestos históricos (Sesión 07).

presupuestos JSON -> JSONStructuralChunker (un componente = un chunk)
-> OpenAIEmbedder (text-embedding-3-small, en batches) -> vectores en
memoria devueltos por POST /embeddings/ingest. Todavía sin persistencia
(pgvector entra en la Sesión 08) ni retrieval.
"""
