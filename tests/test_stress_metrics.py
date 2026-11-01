"""Métricas del stress test (evals/stress/metrics.py): pasa, falla y límite."""

import dataclasses

import pytest

from app.observability.turn_observation import TurnObservation
from evals.stress.metrics import (
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
    MemorySnapshot,
)

BASE = TurnObservation(
    turn_index=3,
    session_id="abcd1234",
    enriched_transcript_chars=500,
    attachments_total_chars=0,
    messages_in_window=6,
    anchors_count=1,
    summary_chars=0,
    tokens_in=1200,
    tokens_out=300,
    cost_usd=0.002,
    latency_ms=1500,
    cache_hit_kind="none",
    last_resolved_tier="fast",
)

MEMORY = MemorySnapshot(
    summary="El proyecto  NIMBUS es una plataforma para academias.",
    anchors=("El presupuesto máximo es de 80000 EUR.",),
    metadata={"project_name": "Kestrel", "mentioned_technologies": ["Flutter", "React"], "agreed_scope": None},
)


def obs(**changes) -> TurnObservation:
    return dataclasses.replace(BASE, **changes)


# ---------------------------------------------------------------- latencia


def test_latency_within_budget_passes():
    result = LatencyBudgetMetric(2000).evaluate(obs(latency_ms=1500))
    assert (result.passed, result.score) == (True, 1.0)


def test_latency_over_budget_fails():
    result = LatencyBudgetMetric(1000).evaluate(obs(latency_ms=1500))
    assert (result.passed, result.score) == (False, 0.0)
    assert "1500 ms > 1000 ms" in result.detail


def test_latency_equal_to_budget_passes():
    assert LatencyBudgetMetric(1500).evaluate(obs(latency_ms=1500)).passed


@pytest.mark.parametrize("budget", [0, -1])
def test_latency_budget_must_be_positive(budget):
    with pytest.raises(ValueError):
        LatencyBudgetMetric(budget)


# ------------------------------------------------------------------ coste


def test_cost_within_budget_passes():
    assert CostBudgetMetric(0.01).evaluate(obs(cost_usd=0.002)).score == 1.0


def test_cost_over_budget_fails():
    result = CostBudgetMetric(0.001).evaluate(obs(cost_usd=0.002))
    assert (result.passed, result.score) == (False, 0.0)


def test_cost_equal_to_budget_passes():
    assert CostBudgetMetric(0.002).evaluate(obs(cost_usd=0.002)).passed


def test_cache_hit_with_zero_cost_passes_a_zero_budget():
    result = CostBudgetMetric(0).evaluate(obs(cost_usd=0.0, cache_hit_kind="exact"))
    assert result.passed


def test_cost_budget_cannot_be_negative():
    with pytest.raises(ValueError):
        CostBudgetMetric(-0.01)


# ------------------------------------------------------------------ drift


def test_fact_in_summary_passes_ignoring_case_and_spaces():
    result = MemoryDriftMetric().evaluate("proyecto nimbus", MEMORY)
    assert (result.passed, result.score) == (True, 1.0)
    assert "en summary" in result.detail


def test_fact_in_metadata_list_passes():
    result = MemoryDriftMetric().evaluate("flutter", MEMORY)
    assert result.passed and "metadata" in result.detail


def test_missing_fact_fails():
    result = MemoryDriftMetric().evaluate("SSO", MEMORY)
    assert (result.passed, result.score) == (False, 0.0)
    assert "no encontrado" in result.detail


def test_paraphrased_number_is_a_failure_not_a_match():
    memory = MemorySnapshot(summary="Presupuesto de 80.000 €.")
    assert not MemoryDriftMetric().evaluate("80000", memory).passed


def test_stale_fact_presence_is_reported():
    result = MemoryDriftMetric().evaluate("Flutter", MEMORY, stale_fact="React")
    assert result.passed and "stale 'React' presente en metadata" in result.detail
    assert MemoryDriftMetric().stale_present("React", MEMORY)


def test_stale_fact_absent_is_reported():
    result = MemoryDriftMetric().evaluate("80000", MEMORY, stale_fact="30000")
    assert result.passed and "stale '30000' ausente" in result.detail


def test_where_restricts_the_search():
    assert not MemoryDriftMetric(where=("summary",)).evaluate("Kestrel", MEMORY).passed
    assert MemoryDriftMetric(where=("metadata",)).evaluate("Kestrel", MEMORY).passed


def test_empty_memory_fails():
    empty = MemorySnapshot(summary=None, anchors=(), metadata=None)
    assert not MemoryDriftMetric().evaluate("Nimbus", empty).passed


@pytest.mark.parametrize("where", [("summary", "history"), ()])
def test_invalid_where_raises(where):
    with pytest.raises(ValueError):
        MemoryDriftMetric(where=where)


def test_empty_fact_raises():
    with pytest.raises(ValueError):
        MemoryDriftMetric().evaluate("  ", MEMORY)


def test_recall_is_the_fraction_of_facts_found():
    metric = MemoryDriftMetric()
    assert metric.recall(["Nimbus", "SSO", "Flutter", "80000"], MEMORY) == 0.75
    assert metric.recall([], MEMORY) is None
