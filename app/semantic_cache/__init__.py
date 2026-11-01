"""Caché semántico de estimaciones (Redis Stack + redisvl).

Clave compuesta: bucket determinista (versión de prompt, tenant y
parámetros estructurados, hasheado) + similitud vectorial de la
descripción normalizada dentro del bucket.

Orden en el servicio: input guardrails -> lookup -> LLM -> output
guardrails -> escritura. Es una optimización, nunca una dependencia
crítica: cualquier fallo de Redis o de embeddings sigue por el LLM.
"""
