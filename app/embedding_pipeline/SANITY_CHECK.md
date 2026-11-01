# Sanity check de embeddings

Modelo: `text-embedding-3-small` (1536 dimensiones). Ejecutado el
2026-10-19 con `uv run python scripts/compare.py --text-a "..." --text-b "..."`.

| Pareja | Texto 1 | Texto 2 | Expectativa | Similitud coseno |
|---|---|---|---|---|
| A (cercanos) | OAuth 2.0 authentication backend with JWT tokens for fintech mobile app | Authorization service using JSON Web Tokens for a banking application | > 0.6 | **0.5957** |
| B (no relacionados) | OAuth 2.0 authentication backend with JWT tokens for fintech mobile app | Database migration from MySQL to PostgreSQL with zero downtime | < 0.4 | **0.1920** |
| C (genéricos) | Backend services | API development | sin expectativa fija | **0.5407** |

## Comentario

- El orden es el esperado: A (0.60) > C (0.54) >> B (0.19). La pareja no
  relacionada queda muy lejos, así que el pipeline discrimina bien lo cercano
  de lo lejano.
- A se queda justo por debajo del umbral orientativo de 0.6. Los dos textos
  dicen lo mismo con vocabulario distinto (OAuth/JWT frente a *authorization*/
  *JSON Web Tokens*, fintech frente a *banking*). El modelo no los trata como
  casi sinónimos: las similitudes de `text-embedding-3-small` se concentran
  en un rango bajo, y los umbrales absolutos hay que calibrarlos para cada
  modelo.
- Lo que más llama la atención es C: dos frases de dos palabras, sin ningún
  término en común, quedan a solo 0.05 de la pareja A. Los textos cortos y
  genéricos caen en una zona "técnica" común del espacio. Para el chunking
  esto significa que un chunk pobre en contenido (un componente con nombre y
  descripción genéricos) puede parecerse a muchas consultas y meter ruido en
  el retrieval. Es otro argumento para añadir el contexto del presupuesto
  padre en cada chunk.
