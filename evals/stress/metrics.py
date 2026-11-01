"""Métricas del stress test del CAG.

Viven aquí y no en evals/metrics.py porque evalúan otra cosa: aquellas
puntúan la estimación final (EstimationResult) de un caso golden; estas
puntúan un turno a partir de su TurnObservation y del snapshot de memoria
de la sesión. Reutilizan MetricResult (no se duplica).

Funciones puras: sin I/O, sin red y sin estado mutable. Match literal sin
distinguir mayúsculas (casefold) y con los espacios normalizados; sin
embeddings ni LLM juez. Una paráfrasis ("80.000 €" por "80000") cuenta como
fallo y así se reporta.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.observability.turn_observation import TurnObservation
from evals.metrics import MetricResult

MemoryField = Literal["summary", "anchors", "metadata"]
MEMORY_FIELDS: tuple[MemoryField, ...] = ("summary", "anchors", "metadata")


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _flatten(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _flatten(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _flatten(v)]
    return [str(value)]


@dataclass(frozen=True)
class MemorySnapshot:
    """Lo que la sesión recuerda tras el turno N (GET /sessions/{id})."""

    summary: str | None = None
    anchors: tuple[str, ...] = ()
    metadata: Mapping[str, Any] | None = field(default=None)

    def texts(self, where: MemoryField) -> list[str]:
        if where == "summary":
            return [self.summary] if self.summary else []
        if where == "anchors":
            return list(self.anchors)
        return _flatten(self.metadata)


def _found_in(text: str, memory: MemorySnapshot, where: Sequence[MemoryField]) -> list[MemoryField]:
    needle = normalize(text)
    return [w for w in where if any(needle in normalize(t) for t in memory.texts(w))]


class LatencyBudgetMetric:
    """1.0 si latency_ms <= budget_ms; 0.0 si no."""

    name = "latency_budget"

    def __init__(self, budget_ms: float) -> None:
        if budget_ms <= 0:
            raise ValueError("budget_ms debe ser > 0")
        self.budget_ms = budget_ms

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        ok = observation.latency_ms <= self.budget_ms
        detail = f"{observation.latency_ms} ms {'<=' if ok else '>'} {self.budget_ms:g} ms"
        return MetricResult(self.name, ok, detail, 1.0 if ok else 0.0)


class CostBudgetMetric:
    """1.0 si cost_usd <= budget_usd; 0.0 si no."""

    name = "cost_budget"

    def __init__(self, budget_usd: float) -> None:
        if budget_usd < 0:
            raise ValueError("budget_usd debe ser >= 0")
        self.budget_usd = budget_usd

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        ok = observation.cost_usd <= self.budget_usd
        detail = f"{observation.cost_usd:.6f} USD {'<=' if ok else '>'} {self.budget_usd:g} USD"
        return MetricResult(self.name, ok, detail, 1.0 if ok else 0.0)


class MemoryDriftMetric:
    """1.0 si el fact del turno k aparece en summary, anchors o ProjectMetadata
    del turno N (N > k); 0.0 si no. Informa también de si el stale_fact
    (el valor sustituido) sigue presente."""

    name = "memory_drift"

    def __init__(self, where: Sequence[MemoryField] = MEMORY_FIELDS) -> None:
        invalid = [w for w in where if w not in MEMORY_FIELDS]
        if invalid or not where:
            raise ValueError(f"where inválido: {list(where)!r} (valores posibles: {MEMORY_FIELDS})")
        self.where: tuple[MemoryField, ...] = tuple(where)

    def evaluate(self, fact: str, memory: MemorySnapshot, stale_fact: str | None = None) -> MetricResult:
        if not fact.strip():
            raise ValueError("fact vacío")
        found = _found_in(fact, memory, self.where)
        detail = f"'{fact}' en {', '.join(found)}" if found else f"'{fact}' no encontrado en {', '.join(self.where)}"
        if stale_fact:
            stale = _found_in(stale_fact, memory, self.where)
            detail += f"; stale '{stale_fact}' presente en {', '.join(stale)}" if stale else f"; stale '{stale_fact}' ausente"
        return MetricResult(self.name, bool(found), detail, 1.0 if found else 0.0)

    def stale_present(self, stale_fact: str, memory: MemorySnapshot) -> bool:
        return bool(_found_in(stale_fact, memory, self.where))

    def recall(self, facts: Sequence[str], memory: MemorySnapshot) -> float | None:
        """Fracción de facts presentes; None si no hay facts que recordar."""
        if not facts:
            return None
        return sum(self.evaluate(f, memory).score or 0.0 for f in facts) / len(facts)
