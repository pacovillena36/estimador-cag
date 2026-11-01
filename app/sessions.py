"""Agregado Session y su almacén en memoria (adaptador de persistencia).

Los modelos de dominio (memoria, metadata, telemetría) viven en
app/conversation/domain; el contrato HTTP, en app/session_schemas.py.

Volatilidad aceptada a propósito en esta fase:
- Es un ejercicio de aprendizaje: el objetivo es la memoria conversacional,
  no la persistencia.
- Las sesiones viven en un diccionario de un único proceso y se pierden al
  reiniciar el servicio (el cliente crea una nueva al recibir un 404).
- No escala horizontalmente: con varios workers cada uno tendría su propio
  diccionario y una sesión solo existiría en uno de ellos. El servicio debe
  ejecutarse con un solo worker (uvicorn sin --workers).
- La persistencia (Redis, BBDD) queda para fases posteriores.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.conversation.domain.memory import ConversationHistory, ConversationMemory
from app.conversation.domain.metadata import ProjectMetadata
from app.conversation.domain.usage import SessionTelemetry
from app.observability.turn_observation import TurnObservation

def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Session:
    session_id: UUID
    memory: ConversationMemory
    created_at: datetime
    last_access_at: datetime
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    telemetry: SessionTelemetry = field(default_factory=SessionTelemetry)
    # Confianza de la última estimación en alcance (señal del tier dinámico).
    last_confidence_pct: int | None = None
    # Observación agregada del último turno completado (evento turn_observed).
    last_turn_observation: TurnObservation | None = None
    # Serializa los turnos de una misma sesión (dos peticiones simultáneas
    # no pueden intercalar historial ni metadata).
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def history(self) -> ConversationHistory:
        return self.memory.history


class SessionCapacityError(Exception):
    """Se ha alcanzado MAX_SESSIONS (-> 503)."""


class SessionNotFoundError(Exception):
    """Sesión inexistente o expirada (-> 404)."""


class SessionStore:
    """Diccionario de sesiones en memoria del proceso.

    Volátil por diseño en esta fase (ver docstring del módulo): un solo
    proceso y un solo worker, se pierde al reiniciar y no se comparte entre
    réplicas. La persistencia queda para fases posteriores.

    Protección de memoria:
    - TTL por inactividad (`ttl`): las sesiones sin acceso se eliminan de
      forma perezosa en create() y get().
    - `max_sessions`: al llegar al máximo, create() lanza
      SessionCapacityError (-> 503) en lugar de expulsar la sesión más
      antigua, que podría ser la conversación activa de otro usuario.
    """

    def __init__(
        self,
        *,
        max_turns: int,
        ttl: timedelta,
        max_sessions: int,
        max_anchors: int = 12,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._sessions: dict[UUID, Session] = {}
        self._max_turns = max_turns
        self._max_anchors = max_anchors
        self._ttl = ttl
        self._max_sessions = max_sessions
        self._clock = clock

    def __len__(self) -> int:
        return len(self._sessions)

    def create(self) -> Session:
        self._purge_expired()
        if len(self._sessions) >= self._max_sessions:
            raise SessionCapacityError()
        now = self._clock()
        # uuid4: aleatorio y no predecible; actúa como token de acceso.
        session = Session(
            session_id=uuid4(),
            memory=ConversationMemory(self._max_turns, self._max_anchors),
            created_at=now,
            last_access_at=now,
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: UUID) -> Session | None:
        self._purge_expired()
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_access_at = self._clock()
        return session

    def delete(self, session_id: UUID) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def _purge_expired(self) -> None:
        limit = self._clock() - self._ttl
        expired = [sid for sid, s in self._sessions.items() if s.last_access_at < limit]
        for session_id in expired:
            del self._sessions[session_id]


def short_session_id(session_id: UUID) -> str:
    """Para logs: el session_id completo es un token de acceso."""
    return str(session_id)[:8]
