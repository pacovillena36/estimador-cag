"""Contrato del evento `turn_observed`: una observación agregada por turno.

Un solo lugar define el shape: el servicio de sesiones la construye y la
emite con asdict(), la API la expone (solo con EXPOSE_TURN_OBSERVATION) y
evals/stress la consume. Solo contadores y métricas: nunca texto de la
transcripción, de los adjuntos, del resumen ni de las anclas.
"""

from dataclasses import dataclass
from typing import Literal

CacheHitKind = Literal["none", "exact", "semantic"]

TURN_OBSERVED_EVENT = "turn_observed"


@dataclass(frozen=True, slots=True)
class TurnObservation:
    turn_index: int  # 1-based, turnos completados dentro de la sesión
    session_id: str  # forma corta (short_session_id): el id completo es un token
    enriched_transcript_chars: int  # mensaje de usuario: transcripción + adjuntos truncados
    attachments_total_chars: int
    messages_in_window: int  # mensajes de historial tras guardar el turno
    anchors_count: int
    summary_chars: int
    tokens_in: int  # todas las llamadas del turno (actor, extractor, summarizer...)
    tokens_out: int
    cost_usd: float
    latency_ms: int  # perf_counter alrededor de todo el turno
    # "exact" si la llamada del actor salió de la caché exact-match del
    # gateway. Las sesiones no usan el caché semántico: nunca "semantic".
    cache_hit_kind: CacheHitKind
    last_resolved_tier: str
