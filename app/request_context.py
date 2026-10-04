"""Contexto de la petición que no viene del body: identidad y aislamiento.

El servicio todavía no es multi-tenant ni tiene autenticación (solo lo
consume el cliente interno por la red de docker-compose), así que todas
las peticiones comparten un tenant constante. Cuando exista autenticación
servicio a servicio, get_request_context debe obtener el tenant del
contexto autenticado (cabecera validada, token...), NUNCA del body: es lo
que garantiza que el caché semántico no sirva a un tenant una respuesta
generada para otro.
"""

from dataclasses import dataclass

SINGLE_TENANT_ID = "single-tenant"


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str


def get_request_context() -> RequestContext:
    return RequestContext(tenant_id=SINGLE_TENANT_ID)
