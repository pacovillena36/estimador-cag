"""Parte determinista de la clave de caché (bucket).

Dos peticiones solo pueden compartir caché si coinciden en versión de
prompt, tenant y los tres parámetros estructurados; la similitud vectorial
se busca únicamente dentro del bucket.

- prompt_version: al promocionar un prompt nuevo, los buckets de la versión
  anterior quedan huérfanos y el TTL los limpia (sin invalidación manual).
- tenant_id: nunca se sirve a un tenant una respuesta generada para otro.

El tag se guarda como SHA-256 de los componentes: evita problemas de escape
en el TAG de Redis y no expone identificadores en claro.
"""

import hashlib

from app.schemas import EstimationRequest

_SEPARATOR = "\x1f"  # separador de unidades ASCII: no aparece en los valores


def build_bucket_key(request: EstimationRequest, *, prompt_version: str, tenant_id: str) -> str:
    raw = _SEPARATOR.join(
        [
            prompt_version,
            tenant_id,
            request.project_type.value,
            request.detail_level.value,
            request.output_format.value,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
