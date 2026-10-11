"""Métricas binarias sobre la estimación final de un caso.

Cada métrica responde pasa / no pasa con un detalle legible. Son
deterministas (sin LLM juez): así el resultado de una eval solo depende de
la salida del sistema evaluado.
"""

import unicodedata
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from app.schemas import MIN_CONFIDENCE_CONTEXT_KEY, EstimationResult
from evals.dataset import GoldenCase


@dataclass(frozen=True)
class MetricResult:
    name: str
    passed: bool
    detail: str


class Metric(Protocol):
    name: str

    def evaluate(self, case: GoldenCase, result: EstimationResult | None, error: str | None) -> MetricResult: ...


def _fold(text: str) -> str:
    """Minúsculas y sin tildes: "Integración" y "integracion" coinciden."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _searchable_text(result: EstimationResult) -> str:
    parts = [result.summary]
    for phase in result.phases:
        parts.append(phase.name)
        parts.extend(phase.assumptions)
    return _fold("\n".join(parts))


class SchemaAdherenceMetric:
    """La salida es un EstimationResult válido (totales coherentes y umbral
    de confianza). Falla si el sistema no devolvió estimación."""

    name = "schema_adherence"

    def __init__(self, min_confidence_pct: int) -> None:
        self._min_confidence_pct = min_confidence_pct

    def evaluate(self, case: GoldenCase, result: EstimationResult | None, error: str | None) -> MetricResult:
        if result is None:
            return MetricResult(self.name, False, f"sin estimación ({error})")
        try:
            EstimationResult.model_validate(
                result.model_dump(), context={MIN_CONFIDENCE_CONTEXT_KEY: self._min_confidence_pct}
            )
        except ValidationError as exc:
            return MetricResult(self.name, False, f"{exc.error_count()} errores de validación")
        return MetricResult(self.name, True, "válida")


class CostBoundsMetric:
    """En alcance: el coste total cae en la horquilla esperada (y no es
    fuera de alcance). Fuera de alcance esperado: el sistema lo declara."""

    name = "cost_bounds"

    def evaluate(self, case: GoldenCase, result: EstimationResult | None, error: str | None) -> MetricResult:
        if result is None:
            return MetricResult(self.name, False, "sin estimación")
        expected = case.expected
        if expected.out_of_scope:
            ok = result.is_out_of_scope
            return MetricResult(self.name, ok, "fuera de alcance" if ok else f"estimó {result.total_cost_eur} €")
        if result.is_out_of_scope:
            return MetricResult(self.name, False, "declaró fuera de alcance un caso estimable")
        bounds = expected.cost_eur
        ok = bounds.min <= result.total_cost_eur <= bounds.max
        return MetricResult(self.name, ok, f"{result.total_cost_eur} € en [{bounds.min}, {bounds.max}]")


class ContentRecallMetric:
    """Proporción de términos esperados que aparecen en el resumen, los
    nombres de fase o las asunciones (cada término admite alternativas)."""

    name = "content_recall"

    def evaluate(self, case: GoldenCase, result: EstimationResult | None, error: str | None) -> MetricResult:
        if result is None:
            return MetricResult(self.name, False, "sin estimación")
        expected = case.expected
        if expected.out_of_scope:
            ok = result.is_out_of_scope
            return MetricResult(self.name, ok, "fuera de alcance" if ok else "debía ser fuera de alcance")
        text = _searchable_text(result)
        found = [t for t in expected.recall_terms if any(_fold(alt) in text for alt in t.split("|"))]
        recall = len(found) / len(expected.recall_terms)
        missing = [t for t in expected.recall_terms if t not in found]
        detail = f"recall {recall:.0%} (mín. {expected.min_recall:.0%})" + (f"; faltan: {', '.join(missing)}" if missing else "")
        return MetricResult(self.name, recall >= expected.min_recall, detail)


def default_metrics(min_confidence_pct: int) -> list[Metric]:
    return [SchemaAdherenceMetric(min_confidence_pct), CostBoundsMetric(), ContentRecallMetric()]
