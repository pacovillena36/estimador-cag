"""Runner de evals: ejecuta cada caso con un CaseExecutor (puerto), aplica
las métricas a la estimación final y agrega acierto, coste y latencia."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Protocol

from app.conversation.domain.review import EstimationMode
from app.schemas import EstimationResult
from evals.dataset import GoldenCase
from evals.metrics import Metric, MetricResult


@dataclass(frozen=True)
class TurnObservation:
    tier: str
    tier_rule: str
    cost_usd: float
    latency_ms: float
    tokens_in: int
    tokens_out: int
    calls: dict[str, int]
    revised: bool | None = None  # solo en modo acb


@dataclass(frozen=True)
class CaseRun:
    result: EstimationResult | None
    turns: tuple[TurnObservation, ...] = ()
    error: str | None = None  # tipo de error si el caso no terminó


class CaseExecutor(Protocol):
    def run(self, case: GoldenCase, mode: EstimationMode) -> CaseRun: ...


@dataclass(frozen=True)
class CaseReport:
    case_id: str
    metrics: tuple[MetricResult, ...]
    turns: tuple[TurnObservation, ...]
    error: str | None

    @property
    def passed(self) -> bool:
        return all(m.passed for m in self.metrics)

    @property
    def cost_usd(self) -> float:
        return round(sum(t.cost_usd for t in self.turns), 6)

    @property
    def latency_ms(self) -> float:
        return round(sum(t.latency_ms for t in self.turns), 1)


@dataclass(frozen=True)
class EvalReport:
    mode: str
    cases: tuple[CaseReport, ...]
    metric_names: tuple[str, ...] = field(default=())

    def pass_rate(self, metric: str | None = None) -> float:
        if not self.cases:
            return 0.0
        if metric is None:
            hits = sum(c.passed for c in self.cases)
        else:
            hits = sum(m.passed for c in self.cases for m in c.metrics if m.name == metric)
        return hits / len(self.cases)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.cases), 6)

    @property
    def mean_latency_ms(self) -> float:
        return round(sum(c.latency_ms for c in self.cases) / len(self.cases), 1) if self.cases else 0.0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "summary": {
                "cases": len(self.cases),
                "all_metrics_pass_rate": self.pass_rate(),
                "pass_rate": {name: self.pass_rate(name) for name in self.metric_names},
                "total_cost_usd": self.total_cost_usd,
                "mean_latency_ms_per_case": self.mean_latency_ms,
            },
            "cases": [
                {
                    "id": c.case_id,
                    "passed": c.passed,
                    "error": c.error,
                    "cost_usd": c.cost_usd,
                    "latency_ms": c.latency_ms,
                    "metrics": [asdict(m) for m in c.metrics],
                    "turns": [asdict(t) for t in c.turns],
                }
                for c in self.cases
            ],
        }


class EvalRunner:
    def __init__(self, executor: CaseExecutor, metrics: Sequence[Metric]) -> None:
        self._executor = executor
        self._metrics = list(metrics)

    def run(self, cases: Sequence[GoldenCase], mode: EstimationMode) -> EvalReport:
        reports = []
        for case in cases:
            run = self._executor.run(case, mode)
            results = tuple(m.evaluate(case, run.result, run.error) for m in self._metrics)
            reports.append(CaseReport(case.id, results, run.turns, run.error))
        return EvalReport(mode.value, tuple(reports), tuple(m.name for m in self._metrics))
