"""Puerto del caché semántico: lo único que conoce el servicio.

El servicio de estimación depende de este Protocol, no de Redis: el
adaptador real (redis_cache.py) y el nulo (noop.py) son intercambiables, y
en los tests se usa un fake en memoria.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class CacheMode(str, Enum):
    # Caché desactivado: ni embedding, ni lookup, ni escritura.
    OFF = "off"
    # Log-only: embedding, lookup y escritura reales, pero siempre se
    # responde con el LLM. Sirve para medir hits y ajustar el threshold.
    SHADOW = "shadow"
    # Un hit válido se devuelve sin llamar al LLM.
    ACTIVE = "active"


@dataclass(frozen=True)
class CachedEntry:
    """Lo que devuelve un hit. `response` es el JSON de EstimationResult tal
    como está en Redis: NO es de confianza y el servicio lo re-valida."""

    response: str
    distance: float


class CacheUnavailableError(Exception):
    """Redis no responde, supera el timeout o el índice no está listo."""


class EstimationCache(Protocol):
    def lookup(self, *, bucket: str, vector: list[float]) -> CachedEntry | None: ...

    def store(self, *, bucket: str, vector: list[float], response: str) -> None: ...
