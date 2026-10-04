"""Estado de las sesiones conversacionales: historial, project_metadata y
almacén en memoria.

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
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_TECHNOLOGIES = 30
MAX_TECHNOLOGY_CHARS = 60
MAX_AGREED_SCOPE_CHARS = 2000
MAX_PROJECT_NAME_CHARS = 200

Technology = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TECHNOLOGY_CHARS)]


class ProjectMetadata(BaseModel):
    """Hechos acumulados del proyecto en curso. La genera el extractor (LLM),
    así que todo se valida aquí: extra="forbid" impide colar campos."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_name: str | None = Field(
        default=None, max_length=MAX_PROJECT_NAME_CHARS, description="Nombre del proyecto."
    )
    assumed_team_size: int | None = Field(
        default=None, ge=1, le=500, description="Tamaño del equipo asumido (personas)."
    )
    mentioned_technologies: list[Technology] = Field(
        default_factory=list,
        max_length=MAX_TECHNOLOGIES,
        description="Tecnologías mencionadas en la conversación.",
    )
    agreed_scope: str | None = Field(
        default=None,
        max_length=MAX_AGREED_SCOPE_CHARS,
        description="Alcance acordado hasta ahora, en pocas frases.",
    )

    def is_empty(self) -> bool:
        return (
            self.project_name is None
            and self.assumed_team_size is None
            and not self.mentioned_technologies
            and not self.agreed_scope
        )


class ConversationHistory:
    """Pares (user, assistant) en una ventana deslizante de `max_turns`.

    Un turno es un par completo: nunca se guarda un mensaje de usuario sin su
    respuesta. El system prompt NO vive aquí: se regenera en cada llamada a
    partir del project_metadata actual, así que siempre va en la posición 0 y
    nunca se descarta al deslizar la ventana.
    """

    def __init__(self, max_turns: int) -> None:
        self._turns: deque[tuple[str, str]] = deque(maxlen=max_turns)

    def __len__(self) -> int:
        return len(self._turns)

    def add_turn(self, user_content: str, assistant_content: str) -> None:
        self._turns.append((user_content, assistant_content))

    def to_messages_list(self, system_prompt: str) -> list[dict[str, str]]:
        """System prompt en primera posición y después los pares en orden
        cronológico. Si el proveedor quiere el system aparte (Anthropic),
        lo separa el cliente LLM (LiteLLM), no esta clase."""
        messages = [{"role": "system", "content": system_prompt}]
        for user_content, assistant_content in self._turns:
            messages.append({"role": "user", "content": user_content})
            messages.append({"role": "assistant", "content": assistant_content})
        return messages


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Session:
    session_id: UUID
    history: ConversationHistory
    created_at: datetime
    last_access_at: datetime
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    # Serializa los turnos de una misma sesión (dos peticiones simultáneas
    # no pueden intercalar historial ni metadata).
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._sessions: dict[UUID, Session] = {}
        self._max_turns = max_turns
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
            history=ConversationHistory(self._max_turns),
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


# ------------------------------------------------------- contrato de la API


class SessionCreated(BaseModel):
    session_id: UUID = Field(description="Identificador de la sesión (actúa como token de acceso).")


class SessionState(BaseModel):
    session_id: UUID
    project_metadata: ProjectMetadata = Field(description="Hechos acumulados del proyecto.")
    turns: int = Field(ge=0, description="Turnos (pares user+assistant) en el historial efectivo.")
