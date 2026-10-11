"""Framework de evals sin LLM: dataset, métricas, runner y ejecutor real con
un LLM falso."""

import pytest

from app.conversation.domain.review import EstimationMode
from app.guardrails.pipeline import build_input_pipeline, build_output_pipeline
from app.schemas import OUT_OF_SCOPE_PREFIX, EstimationResult
from evals.dataset import GoldenCase, load_dataset
from evals.executor import ServiceCaseExecutor
from evals.metrics import ContentRecallMetric, CostBoundsMetric, SchemaAdherenceMetric, default_metrics
from evals.runner import CaseRun, EvalRunner, TurnObservation
from tests.conftest import VALID_RESULT, FakeModeration, make_settings
from tests.sessions.test_session_turns import FakeLLM

RESULT = EstimationResult.model_validate(VALID_RESULT)  # 6600 €, fases Diseño y Desarrollo
OUT_OF_SCOPE = EstimationResult.model_validate(
    {"summary": f"{OUT_OF_SCOPE_PREFIX} no es software.", "total_hours": 0, "total_duration_weeks": 0,
     "total_cost_eur": 0, "confidence_pct": 0, "phases": []}
)


def case(**overrides) -> GoldenCase:
    base = {
        "id": "c",
        "description": "d",
        "project_type": "web_saas",
        "turns": ["t"],
        "expected": {"cost_eur": {"min": 5000, "max": 8000}, "recall_terms": ["diseño", "desarrollo|backend", "pagos"]},
    }
    return GoldenCase.model_validate(base | overrides)


OOS_CASE = case(expected={"out_of_scope": True})


# ------------------------------------------------------------------ dataset


def test_golden_dataset_has_16_valid_cases_with_memory_and_out_of_scope_cases():
    cases = load_dataset().cases
    assert len(cases) == 16
    assert sum(c.expected.out_of_scope for c in cases) == 2
    assert any(c.window and len(c.turns) > c.window for c in cases)  # fuerzan expulsión


def test_in_scope_case_requires_bounds_and_terms():
    with pytest.raises(ValueError):
        case(expected={"recall_terms": ["x"]})


# ----------------------------------------------------------------- métricas


def test_schema_adherence():
    metric = SchemaAdherenceMetric(min_confidence_pct=30)
    assert metric.evaluate(case(), RESULT, None).passed
    assert not metric.evaluate(case(), None, "InvalidStructuredOutputError").passed


@pytest.mark.parametrize(
    ("golden", "result", "passed"),
    [
        (case(), RESULT, True),
        (case(expected={"cost_eur": {"min": 7000, "max": 9000}, "recall_terms": ["x"]}), RESULT, False),
        (case(), OUT_OF_SCOPE, False),
        (OOS_CASE, OUT_OF_SCOPE, True),
        (OOS_CASE, RESULT, False),
    ],
)
def test_cost_bounds(golden, result, passed):
    assert CostBoundsMetric().evaluate(golden, result, None).passed is passed


def test_content_recall_matches_alternatives_without_accents_and_reports_missing():
    metric = ContentRecallMetric()
    partial = metric.evaluate(case(), RESULT, None)  # diseño y desarrollo sí, pagos no: 67 %
    assert partial.passed and "faltan: pagos" in partial.detail
    strict = case(expected={"cost_eur": {"min": 1, "max": 2}, "recall_terms": ["DISENO", "pagos"], "min_recall": 0.6})
    assert not metric.evaluate(strict, RESULT, None).passed  # 50 %
    assert metric.evaluate(OOS_CASE, OUT_OF_SCOPE, None).passed


# ------------------------------------------------------------------- runner


class StaticExecutor:
    def __init__(self, runs: dict[str, CaseRun]) -> None:
        self.runs = runs
        self.modes: list[EstimationMode] = []

    def run(self, golden, mode):
        self.modes.append(mode)
        return self.runs[golden.id]


def test_runner_aggregates_pass_rates_cost_and_latency():
    turn = TurnObservation("fast", "default", 0.01, 500.0, 100, 10, {"actor": 1})
    executor = StaticExecutor({
        "ok": CaseRun(RESULT, (turn, turn)),
        "oos": CaseRun(RESULT, (turn,)),
        "err": CaseRun(None, (), "AllProvidersFailedError"),
    })
    cases = [case(id="ok"), case(id="oos", expected={"out_of_scope": True}), case(id="err")]

    report = EvalRunner(executor, default_metrics(30)).run(cases, EstimationMode.ACB)

    assert executor.modes == [EstimationMode.ACB] * 3
    assert report.pass_rate("schema_adherence") == pytest.approx(2 / 3)
    assert report.pass_rate("cost_bounds") == pytest.approx(1 / 3)
    assert report.pass_rate() == pytest.approx(1 / 3)
    assert report.total_cost_usd == 0.03 and report.mean_latency_ms == pytest.approx(500.0)
    data = report.to_dict()
    assert data["mode"] == "acb" and data["cases"][2]["error"] == "AllProvidersFailedError"


# -------------------------------------------- ejecutor real con LLM falso


@pytest.mark.parametrize("mode", [EstimationMode.ACTOR, EstimationMode.ACB])
def test_service_executor_runs_every_turn_through_the_real_service(mode):
    settings = make_settings()
    llm = FakeLLM()
    executor = ServiceCaseExecutor(
        settings, llm, build_input_pipeline(settings, FakeModeration()), build_output_pipeline(settings)
    )
    golden = case(turns=["Turno uno con presupuesto de 5.000 €.", "Turno dos.", "Turno tres."], window=1)

    run = executor.run(golden, mode)

    assert run.error is None and run.result.total_hours == 120
    assert len(run.turns) == 3 and len(llm.estimation_calls) == 3
    assert len(llm.summary_calls) == 2  # ventana de 1: expulsa en los turnos 2 y 3
    assert run.turns[0].calls["actor"] == 1
    assert ("critic" in run.turns[0].calls) is (mode is EstimationMode.ACB)
