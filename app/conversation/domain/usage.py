"""Uso del LLM por turno y acumulado por sesión.

Cada llamada de un turno (actor, critic, boss, summarizer, extractor) queda
registrada con su rol, para saber cuánto cuesta un turno de verdad y en qué
se va el gasto.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.conversation.domain.tiering import TierDecision
from app.ports.llm import LLMUsage


class CallRole(StrEnum):
    ACTOR = "actor"
    CRITIC = "critic"
    BOSS = "boss"
    SUMMARIZER = "summarizer"
    METADATA_EXTRACTOR = "metadata_extractor"


@dataclass(frozen=True)
class CallRecord:
    role: CallRole
    usage: LLMUsage


def _sum(values: list[float | int | None]) -> float:
    return sum(v for v in values if v is not None)


@dataclass(frozen=True)
class TurnUsage:
    calls: tuple[CallRecord, ...] = ()

    @property
    def cost_usd(self) -> float:
        return round(_sum([c.usage.cost_usd for c in self.calls]), 6)

    @property
    def tokens_in(self) -> int:
        return int(_sum([c.usage.tokens_in for c in self.calls]))

    @property
    def tokens_out(self) -> int:
        return int(_sum([c.usage.tokens_out for c in self.calls]))

    @property
    def latency_ms(self) -> float:
        return round(_sum([c.usage.latency_ms for c in self.calls]), 1)

    def by_role(self, role: CallRole) -> tuple[CallRecord, ...]:
        return tuple(c for c in self.calls if c.role is role)

    def count_by_role(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for call in self.calls:
            counts[call.role.value] = counts.get(call.role.value, 0) + 1
        return counts


@dataclass
class SessionTelemetry:
    """Observaciones acumuladas de una sesión (solo contadores, nunca
    contenido de la conversación)."""

    last_tier: TierDecision | None = None
    last_turn: TurnUsage | None = None
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0

    def record(self, decision: TierDecision, usage: TurnUsage) -> None:
        self.last_tier = decision
        self.last_turn = usage
        self.total_cost_usd = round(self.total_cost_usd + usage.cost_usd, 6)
        self.total_tokens_in += usage.tokens_in
        self.total_tokens_out += usage.tokens_out
