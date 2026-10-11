"""Contrato HTTP de las sesiones conversacionales (lo reutiliza el cliente
Streamlit). Solo contadores y metadata: nunca el texto del resumen ni de las
anclas, que pueden contener datos del cliente."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.conversation.domain.metadata import ProjectMetadata


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
