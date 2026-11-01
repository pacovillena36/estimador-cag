"""Contrato HTTP de las sesiones conversacionales (lo reutiliza el cliente
Streamlit). Solo contadores y metadata: nunca el texto del resumen ni de las
anclas, que pueden contener datos del cliente. La única excepción es
`memory_debug`, que solo aparece con EXPOSE_SESSION_MEMORY_DEBUG (evals en
local, desactivado por defecto)."""

from uuid import UUID

from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from app.conversation.domain.metadata import ProjectMetadata
from app.observability.turn_observation import TurnObservation


class SessionCreated(BaseModel):
    session_id: UUID = Field(description="Identificador de la sesión (actúa como token de acceso).")


class SessionUsage(BaseModel):
    last_turn_cost_usd: float | None = Field(default=None, description="Coste del último turno (todas sus llamadas).")
    last_turn_tokens_in: int | None = None
    last_turn_tokens_out: int | None = None
    last_turn_latency_ms: float | None = Field(default=None, description="Suma de la latencia de las llamadas del último turno.")
    last_turn_calls: dict[str, int] = Field(default_factory=dict, description="Llamadas del último turno por rol.")
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class SessionMemoryDebug(BaseModel):
    summary: str = Field(description="Resumen acumulado (texto).")
    anchors: list[str] = Field(default_factory=list, description="Texto de las anclas.")


class SessionState(BaseModel):
    session_id: UUID
    project_metadata: ProjectMetadata = Field(description="Hechos acumulados del proyecto.")
    turns: int = Field(ge=0, description="Turnos (pares user+assistant) en el historial efectivo.")
    message_count: int = Field(default=0, ge=0, description="Mensajes de historial que viajan al LLM.")
    anchors_count: int = Field(default=0, ge=0, description="Anclas de turnos ya expulsados de la ventana.")
    summary_chars: int = Field(default=0, ge=0, description="Longitud del resumen acumulado.")
    last_resolved_tier: str | None = Field(default=None, description="Tier del último turno (fast | strong).")
    last_tier_rule: str | None = Field(default=None, description="Regla que decidió ese tier.")
    usage: SessionUsage = Field(default_factory=SessionUsage)
    last_turn_observation: TurnObservation | None = Field(
        default=None, description="Solo con EXPOSE_TURN_OBSERVATION (contadores del último turno)."
    )
    memory_debug: SessionMemoryDebug | None = Field(
        default=None, description="Solo con EXPOSE_SESSION_MEMORY_DEBUG (texto de resumen y anclas)."
    )

    @model_serializer(mode="wrap")
    def _omit_disabled_debug(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Con los flags desactivados (producción) las claves de depuración
        # ni siquiera aparecen: el contrato público no cambia.
        data = handler(self)
        for key in ("last_turn_observation", "memory_debug"):
            if data.get(key) is None:
                data.pop(key, None)
        return data
