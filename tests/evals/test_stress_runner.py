"""Runner del stress test sin red ni LLM: filas, validaciones del CLI y la
orquestación con un cliente falso."""

import asyncio

import pytest

from evals.stress.client import ApiResult
from evals.stress.fixtures.build_pdfs import Canary, FixtureInfo
from evals.stress.run import _build_config, _parse_args, _validate_output, _validate_url
from evals.stress.runner import (
    CSV_COLUMNS,
    OBSERVATION_FIELDS,
    SetupError,
    StressConfig,
    StressRunner,
    build_row,
    canaries_sent,
)
from evals.stress.scenarios import CONTRADICTION, GROWING, PIVOT, SCENARIOS, expected_facts, stale_facts

FIXTURE = FixtureInfo(
    filename="attach_50kb.pdf",
    size_kb=50,
    target_chars=50_000,
    extracted_chars=50_000,
    cap_chars=20_000,
    canaries=(
        Canary("START", "CANARY-START-AAAA", 10),
        Canary("AFTER-CAP", "CANARY-AFTER-CAP-BBBB", 20_300),
    ),
)


def config(**changes) -> StressConfig:
    base = dict(
        scenarios=(CONTRADICTION,),
        attachment_sizes_kb=(0,),
        repeats=1,
        max_turns=3,
        latency_budget_ms=1000,
        cost_budget_usd=0.01,
        max_total_cost_usd=1.0,
    )
    return StressConfig(**(base | changes))


def observation(turn_index: int, **changes) -> dict:
    return {
        "turn_index": turn_index,
        "session_id": "abcd1234",
        "enriched_transcript_chars": 100,
        "attachments_total_chars": 0,
        "messages_in_window": 2 * turn_index,
        "anchors_count": 0,
        "summary_chars": 0,
        "tokens_in": 1000,
        "tokens_out": 100,
        "cost_usd": 0.001,
        "latency_ms": 800,
        "cache_hit_kind": "none",
        "last_resolved_tier": "fast",
    } | changes


# ------------------------------------------------------------- escenarios


def test_scenarios_have_20_turns_and_track_the_contradiction():
    assert all(len(s.turns) == 20 for s in SCENARIOS.values())
    facts = [f.fact for f in expected_facts(CONTRADICTION, 10)]
    assert "80000" in facts and "30000" not in facts
    assert [f.fact for f in expected_facts(CONTRADICTION, 5)].count("30000") == 1
    assert stale_facts(CONTRADICTION, 7) == () and stale_facts(CONTRADICTION, 8) == ("30000",)
    assert stale_facts(PIVOT, 20) == ("React",)
    assert GROWING.turn(1).fact_to_remember == "Nimbus"


# ------------------------------------------------------------------- filas


def test_row_has_the_fixed_columns_and_metrics():
    from evals.stress.metrics import MemorySnapshot

    memory = MemorySnapshot(summary="Proyecto Kestrel con 30000 EUR.", metadata={"project_name": "Kestrel"})
    row = build_row(
        scenario=CONTRADICTION,
        attachment_kb=0,
        repeat=1,
        turn=3,
        observation=observation(3, latency_ms=1500),
        memory=memory,
        fixture=None,
        sent_canaries=None,
        config=config(),
        http_status=200,
        error=None,
    )
    assert tuple(row) == CSV_COLUMNS
    assert {k: row[k] for k in OBSERVATION_FIELDS} == observation(3, latency_ms=1500)
    assert (row["latency_ok"], row["cost_ok"]) == (False, True)
    assert row["drift_recall"] == 0.5  # Kestrel sí, "número de serie" no
    assert row["stale_fact_present"] == "" and row["first_fact_found"] is True
    assert row["checkpoint"] is True and row["facts_expected"] == 2


def test_failed_turn_row_keeps_the_error_and_blank_metrics():
    row = build_row(
        scenario=GROWING,
        attachment_kb=5,
        repeat=2,
        turn=4,
        observation=None,
        memory=None,
        fixture=FIXTURE,
        sent_canaries=1,
        config=config(),
        http_status=502,
        error="http_502:estimation_failed",
    )
    assert row["error"] == "http_502:estimation_failed" and row["turn_index"] == ""
    assert row["latency_ok"] == "" and row["attachment_canaries_sent"] == 1


def test_canaries_sent_follow_the_truncation():
    assert canaries_sent(FIXTURE, 20_000) == 1
    assert canaries_sent(FIXTURE, 50_000) == 2


# -------------------------------------------------------------------- CLI


def test_defaults_plan_900_calls():
    assert _build_config(_parse_args([])).planned_turns == 900


@pytest.mark.parametrize(
    "argv", [["--attachment-sizes", "7"], ["--scenarios", "otro"], ["--max-turns", "21"], ["--latency-budget-ms", "0"]]
)
def test_invalid_arguments_are_rejected(argv):
    with pytest.raises(ValueError):
        _build_config(_parse_args(argv))


def test_remote_hosts_need_allow_remote():
    assert _validate_url("http://localhost:8000", False)
    with pytest.raises(ValueError):
        _validate_url("https://api.example.com", False)
    assert _validate_url("https://api.example.com", True)


def test_output_must_stay_inside_evals_stress(tmp_path):
    assert _validate_output(_validate_output.__globals__["DEFAULT_OUTPUT"]).name == "results.csv"
    for bad in (tmp_path / "x.csv", _validate_output.__globals__["STRESS_DIR"] / "x.txt"):
        with pytest.raises(ValueError):
            _validate_output(bad)


# ----------------------------------------------------------- orquestación


class FakeClient:
    """Sesión en memoria: cada turno cuesta 0.001 USD; `fail_turns` fallan."""

    def __init__(self, fail_turns=(), expose=True) -> None:
        self.fail_turns = set(fail_turns)
        self.expose = expose
        self.completed = 0
        self.attachments: list[str | None] = []

    async def create_session(self):
        self.completed = 0
        return ApiResult(201, {"session_id": "s-1"})

    async def estimate(self, session_id, transcript, project_type, attachment):
        self.attachments.append(attachment.filename if attachment else None)
        if len(self.attachments) in self.fail_turns:
            return ApiResult(502, {"error": {"code": "estimation_failed"}}, "http_502:estimation_failed")
        self.completed += 1
        return ApiResult(200, {"result": {}})

    async def state(self, session_id):
        body = {"project_metadata": {"project_name": "Kestrel"}}
        if self.expose:
            body["last_turn_observation"] = observation(self.completed) if self.completed else None
            body["memory_debug"] = {"summary": "", "anchors": []}
        return ApiResult(200, body)


def run(client, cfg, tmp_path, fixtures=None):
    rows: list[dict] = []
    runner = StressRunner(client, cfg, fixtures or {}, tmp_path, rows.append)
    return asyncio.run(runner.run()), rows


def test_one_row_per_turn_and_failures_do_not_abort(tmp_path):
    summary, rows = run(FakeClient(fail_turns={2}), config(), tmp_path)
    assert [r["turn"] for r in rows] == [1, 2, 3]
    assert rows[1]["error"] and rows[1]["turn_index"] == ""
    assert rows[2]["turn_index"] == 2  # el turno fallido no avanza la sesión
    assert (summary.rows, summary.failed_turns) == (3, 1)


def test_attachment_only_on_turn_1(tmp_path):
    (tmp_path / FIXTURE.filename).write_bytes(b"%PDF-")
    client = FakeClient()
    run(client, config(attachment_sizes_kb=(50,)), tmp_path, {50: FIXTURE})
    assert client.attachments == ["attach_50kb.pdf", None, None]


def test_aborts_when_total_cost_exceeds_the_limit(tmp_path):
    summary, rows = run(FakeClient(), config(max_turns=20, max_total_cost_usd=0.0025), tmp_path)
    assert summary.aborted and len(rows) == 3


def test_missing_flags_are_a_setup_error(tmp_path):
    with pytest.raises(SetupError):
        run(FakeClient(expose=False), config(), tmp_path)


# ----------------------------------------------------------------- reporte


def test_report_renders_from_csv_rows_and_keeps_manual_text(tmp_path):
    import csv

    from evals.stress import report

    _, rows = run(FakeClient(fail_turns={2}), config(max_turns=6), tmp_path)
    path = tmp_path / "results.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    with path.open(encoding="utf-8", newline="") as handle:
        parsed = list(csv.DictReader(handle))

    text = report.render(parsed, path)
    assert "| Presupuesto corregido | sin documento | 5 de 6 (cortada en el 6) |" in text
    assert "1 × `http_502:estimation_failed`" in text
    assert "| Presupuesto corregido | 3 |" in text and "| Presupuesto corregido | 6 |" in text

    md = tmp_path / "REPORT.md"
    md.write_text(f"# R\n\nantes\n\n{report.BEGIN}\nviejo\n{report.END}\n\ndespués\n", encoding="utf-8")
    report.write_report(text, md)
    result = md.read_text(encoding="utf-8")
    assert "antes" in result and "después" in result and "viejo" not in result


def test_percentile_handles_small_samples():
    from evals.stress.report import percentile

    assert percentile([], 95) is None and percentile([7.0], 95) == 7.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
