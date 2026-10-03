"""Tests del contrato de salida del LLM (EstimationResult). No se prueba el
modelo, se prueba el contrato: lo que pase estos validadores es lo único
que puede llegar al cliente."""

import pytest
from pydantic import ValidationError

from app.schemas import EstimationResponse, EstimationResult, Phase
from tests.conftest import VALID_RESULT


def _phase(name, weeks, cost, hours=10):
    return Phase(
        name=name,
        hours=hours,
        duration_weeks=weeks,
        cost_eur=cost,
        confidence_pct=80,
        assumptions=[],
    )


def _result(phases, *, weeks=None, cost=None, hours=None, **overrides):
    fields = {
        "summary": "ok",
        "total_hours": hours if hours is not None else sum(p.hours for p in phases),
        "total_duration_weeks": weeks if weeks is not None else sum(p.duration_weeks for p in phases),
        "total_cost_eur": cost if cost is not None else sum(p.cost_eur for p in phases),
        "confidence_pct": 80,
        "phases": phases,
    }
    return EstimationResult(**(fields | overrides))


def test_valid_estimation_passes():
    EstimationResult(
        summary="ok", total_hours=20, total_duration_weeks=10, total_cost_eur=12000,
        confidence_pct=80,
        phases=[_phase("Design", 4, 4000), _phase("Build", 6, 8000)],
    )


def test_total_cost_must_match_phases():
    # 4000 + 8000 = 12000, pero el total dice 10000 -> debe fallar
    with pytest.raises(ValidationError, match="total_cost_eur"):
        EstimationResult(
            summary="Test", total_hours=20, total_duration_weeks=10, total_cost_eur=10000,
            confidence_pct=80,
            phases=[_phase("Design", 4, 4000), _phase("Build", 6, 8000)],
        )


def test_total_cost_tolerates_five_percent():
    phases = [_phase("Design", 4, 4000), _phase("Build", 6, 8000)]
    _result(phases, cost=12600)  # 12000 vs 12600: 4,8 %
    with pytest.raises(ValidationError, match="total_cost_eur"):
        _result(phases, cost=11400)  # 12000 vs 11400: 5,3 %


@pytest.mark.parametrize(("total_weeks", "valid"), [(10, True), (9, True), (11, True), (12, False), (8, False)])
def test_total_duration_tolerates_one_week(total_weeks, valid):
    phases = [_phase("Design", 4, 4000), _phase("Build", 6, 8000)]  # 10 semanas
    if valid:
        _result(phases, weeks=total_weeks)
    else:
        with pytest.raises(ValidationError, match="total_duration_weeks"):
            _result(phases, weeks=total_weeks)


def test_total_hours_must_match_phases_exactly():
    phases = [_phase("Design", 4, 4000, hours=30), _phase("Build", 6, 8000, hours=50)]
    _result(phases, hours=80)
    with pytest.raises(ValidationError, match="total_hours"):
        _result(phases, hours=81)


def test_zero_total_cost_does_not_divide_by_zero():
    _result([_phase("Free", 2, 0)], cost=0)
    with pytest.raises(ValidationError, match="total_cost_eur"):
        _result([_phase("Paid", 2, 500)], cost=0)


@pytest.mark.parametrize(
    "build",
    [
        lambda: _result([_phase("A", 2, 100)], confidence_pct=101),
        lambda: _result([_phase("A", 2, 100)], confidence_pct=-1),
        lambda: _phase("A", 0, 100),  # duration_weeks = 0
        lambda: _phase("A", 53, 100),  # duration_weeks > 52
        lambda: _phase("A", 2, -1),  # coste negativo
        lambda: _phase("A", 2, 100, hours=0),
        lambda: Phase(name="A", hours=1, duration_weeks=1, cost_eur=1, confidence_pct=101, assumptions=[]),
        lambda: _result([], weeks=1, cost=0, hours=1),  # sin fases
    ],
)
def test_field_constraints_are_enforced(build):
    with pytest.raises(ValidationError):
        build()


def test_validator_messages_are_in_english_for_the_llm():
    with pytest.raises(ValidationError) as exc:
        _result([_phase("A", 2, 100)], cost=999)
    assert "does not match" in str(exc.value)


def test_response_wraps_the_result_and_prompt_version():
    response = EstimationResponse(
        result=EstimationResult.model_validate(VALID_RESULT), prompt_version="v1"
    )
    assert response.model_dump(mode="json") == {"result": VALID_RESULT, "prompt_version": "v1"}


def test_json_schema_documents_constraints_for_the_llm():
    schema = EstimationResult.model_json_schema()
    phase = schema["$defs"]["Phase"]["properties"]
    assert phase["confidence_pct"]["maximum"] == 100
    assert phase["duration_weeks"]["minimum"] == 1
    assert schema["properties"]["phases"]["minItems"] == 1
    assert all("description" in field for field in schema["properties"].values())
