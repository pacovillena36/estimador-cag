"""Orquestación del stress test: conversaciones -> filas del CSV.

Separado del CLI (run.py) y del transporte (client.py): recibe una config
inmutable y un SessionApiClient, y entrega cada fila a un `sink` en cuanto
termina el turno (el CSV se va escribiendo con flush: si la ejecución se
corta, el parcial sirve).

Decisiones:
- El adjunto se envía solo en el turno 1 de cada conversación; el resto de
  turnos van sin adjunto. Así el adjunto viaja literal en la ventana
  mientras su turno siga dentro y después solo sobrevive en la memoria
  (resumen, anclas, metadata), que es lo que mide attachment_recall.
- turn_observed se lee de GET /sessions/{id} (last_turn_observation, tras
  EXPOSE_TURN_OBSERVATION) y la memoria de memory_debug (tras
  EXPOSE_SESSION_MEMORY_DEBUG). No se parsean logs.
- Las repeticiones usan transcripts idénticos: los cache hits exact son
  comportamiento real del sistema y no se evitan.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from app.observability.turn_observation import TurnObservation
from evals.stress.client import Attachment, SessionApiClient
from evals.stress.fixtures.build_pdfs import FixtureInfo
from evals.stress.metrics import CostBudgetMetric, LatencyBudgetMetric, MemoryDriftMetric, MemorySnapshot
from evals.stress.scenarios import CHECKPOINTS, Scenario, Turn, expected_facts, stale_facts

OBSERVATION_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(TurnObservation))
CSV_COLUMNS: tuple[str, ...] = (
    "scenario",
    "attachment_kb",
    "repeat",
    *OBSERVATION_FIELDS,
    "latency_ok",
    "cost_ok",
    "drift_recall",
    "stale_fact_present",
    "attachment_recall",
    # Columnas añadidas (ver REPORT.md):
    "turn",  # turno del escenario (también si el turno falla y no hay observación)
    "checkpoint",  # N ∈ {1, 3, 6, 10, 20}
    "facts_expected",  # facts vigentes de turnos k < N
    "first_fact_found",  # el fact del turno 1 (nombre del proyecto) sigue en memoria
    "attachment_canaries_sent",  # canarios que sobrevivieron al truncado del adjunto
    "attachment_canaries_total",
    "http_status",
    "error",
)

Row = dict[str, Any]


@dataclass(frozen=True)
class StressConfig:
    scenarios: tuple[Scenario, ...]
    attachment_sizes_kb: tuple[int, ...]
    repeats: int
    max_turns: int
    latency_budget_ms: float
    cost_budget_usd: float
    max_total_cost_usd: float

    @property
    def planned_turns(self) -> int:
        return len(self.scenarios) * len(self.attachment_sizes_kb) * self.repeats * self.max_turns


class SetupError(Exception):
    """La API no expone lo necesario (flags desactivados): no tiene sentido seguir."""


@dataclass
class RunSummary:
    rows: int = 0
    failed_turns: int = 0
    total_cost_usd: float = 0.0
    aborted: str | None = None


def memory_snapshot(state: dict[str, Any]) -> MemorySnapshot:
    debug = state.get("memory_debug") or {}
    return MemorySnapshot(
        summary=debug.get("summary"),
        anchors=tuple(debug.get("anchors") or ()),
        metadata=state.get("project_metadata"),
    )


def canaries_sent(fixture: FixtureInfo, attachments_total_chars: int) -> int:
    """El truncado conserva el prefijo: un canario llegó al LLM si termina
    antes del número de caracteres del adjunto tras truncar."""
    return sum(1 for c in fixture.canaries if c.offset + len(c.token) <= attachments_total_chars)


def build_row(
    *,
    scenario: Scenario,
    attachment_kb: int,
    repeat: int,
    turn: int,
    observation: dict[str, Any] | None,
    memory: MemorySnapshot | None,
    fixture: FixtureInfo | None,
    sent_canaries: int | None,
    config: StressConfig,
    http_status: int | None,
    error: str | None,
) -> Row:
    """Pura: una fila del CSV a partir de lo observado tras un turno."""
    row: Row = dict.fromkeys(CSV_COLUMNS, "")
    facts = expected_facts(scenario, turn)
    row |= {
        "scenario": scenario.name,
        "attachment_kb": attachment_kb,
        "repeat": repeat,
        "turn": turn,
        "checkpoint": turn in CHECKPOINTS,
        "facts_expected": len(facts),
        "http_status": "" if http_status is None else http_status,
        "error": error or "",
    }
    if fixture is not None:
        row["attachment_canaries_total"] = len(fixture.canaries)
        row["attachment_canaries_sent"] = "" if sent_canaries is None else sent_canaries
    if observation is not None:
        row |= {name: observation[name] for name in OBSERVATION_FIELDS}
        obs = TurnObservation(**{name: observation[name] for name in OBSERVATION_FIELDS})
        row["latency_ok"] = LatencyBudgetMetric(config.latency_budget_ms).evaluate(obs).passed
        row["cost_ok"] = CostBudgetMetric(config.cost_budget_usd).evaluate(obs).passed
    if memory is not None:
        drift = MemoryDriftMetric()
        recall = drift.recall([f.fact for f in facts], memory)
        row["drift_recall"] = "" if recall is None else round(recall, 4)
        stale = stale_facts(scenario, turn)
        row["stale_fact_present"] = any(drift.stale_present(s, memory) for s in stale) if stale else ""
        first = scenario.turn(1).fact_to_remember
        if first and turn > 1:
            row["first_fact_found"] = drift.evaluate(first, memory).passed
        if fixture is not None:
            row["attachment_recall"] = round(drift.recall([c.token for c in fixture.canaries], memory) or 0.0, 4)
    return row


class StressRunner:
    def __init__(
        self,
        client: SessionApiClient,
        config: StressConfig,
        fixtures: dict[int, FixtureInfo],
        fixture_dir: Path,
        sink: Callable[[Row], None],
        progress: Callable[[str], None] = lambda _: None,
    ) -> None:
        self._client = client
        self._config = config
        self._fixtures = fixtures
        self._fixture_dir = fixture_dir
        self._sink = sink
        self._progress = progress
        self.summary = RunSummary()

    async def run(self) -> RunSummary:
        config = self._config
        for scenario in config.scenarios:
            for kb in config.attachment_sizes_kb:
                for repeat in range(1, config.repeats + 1):
                    await self._conversation(scenario, kb, repeat)
                    if self.summary.aborted:
                        return self.summary
        return self.summary

    async def _conversation(self, scenario: Scenario, kb: int, repeat: int) -> None:
        created = await self._client.create_session()
        if not created.ok or not created.body:
            self.summary.failed_turns += 1
            self._emit(scenario=scenario, kb=kb, repeat=repeat, turn=1, status=created.status, error=f"create:{created.error}")
            return
        session_id = str(created.body["session_id"])
        fixture = self._fixtures.get(kb) if kb else None
        attachment = (
            Attachment(fixture.filename, (self._fixture_dir / fixture.filename).read_bytes()) if fixture else None
        )
        sent: int | None = None
        completed = 0

        for turn in scenario.turns[: self._config.max_turns]:
            index = turn.turn_index
            result = await self._client.estimate(
                session_id, turn.transcript, scenario.project_type, attachment if index == 1 else None
            )
            state = await self._client.state(session_id)
            body = state.body if state.ok and state.body else {}
            if result.ok and "last_turn_observation" not in body:
                raise SetupError("GET /sessions/{id} no trae last_turn_observation: activa EXPOSE_TURN_OBSERVATION=true")
            if result.ok and "memory_debug" not in body:
                raise SetupError("GET /sessions/{id} no trae memory_debug: activa EXPOSE_SESSION_MEMORY_DEBUG=true")

            completed += result.ok
            observation = body.get("last_turn_observation") if result.ok else None
            if observation is not None and observation.get("turn_index") != completed:
                observation = None  # no es la observación de este turno
            if not result.ok:
                self.summary.failed_turns += 1
            if observation is not None:
                self.summary.total_cost_usd += float(observation["cost_usd"])
                if fixture is not None and index == 1:
                    sent = canaries_sent(fixture, int(observation["attachments_total_chars"]))
            memory = memory_snapshot(body) if body else None
            self._emit(
                scenario=scenario,
                kb=kb,
                repeat=repeat,
                turn=index,
                observation=observation,
                memory=memory,
                fixture=fixture,
                sent=sent,
                status=result.status,
                error=result.error,
            )
            self._progress(
                f"{scenario.name} kb={kb} rep={repeat} turno {index}: "
                f"{'ok' if result.ok else result.error} · acumulado {self.summary.total_cost_usd:.4f} USD"
            )
            if self.summary.total_cost_usd > self._config.max_total_cost_usd:
                self.summary.aborted = (
                    f"coste acumulado {self.summary.total_cost_usd:.4f} USD > "
                    f"--max-total-cost-usd {self._config.max_total_cost_usd}"
                )
                return

    def _emit(
        self,
        *,
        scenario: Scenario,
        kb: int,
        repeat: int,
        turn: int,
        observation: dict[str, Any] | None = None,
        memory: MemorySnapshot | None = None,
        fixture: FixtureInfo | None = None,
        sent: int | None = None,
        status: int | None = None,
        error: str | None = None,
    ) -> None:
        row = build_row(
            scenario=scenario,
            attachment_kb=kb,
            repeat=repeat,
            turn=turn,
            observation=observation,
            memory=memory,
            fixture=fixture,
            sent_canaries=sent,
            config=self._config,
            http_status=status,
            error=error,
        )
        self._sink(row)
        self.summary.rows += 1


TurnPrice = Callable[[Turn, int, bool], tuple[float, float]]


def estimate_cost_usd(
    config: StressConfig,
    *,
    price_for_turn: TurnPrice,
    system_prompt_chars: int,
    attachment_chars: Callable[[int], int],
    window_turns: int,
) -> float:
    """Estimación aproximada para --dry-run, sin llamar a nada.

    Por turno: el actor recibe system + ventana (pares previos con su
    respuesta JSON, ~1500 caracteres) + transcripción, y el adjunto del
    turno 1 mientras siga en la ventana. Se suma un 50 % por el extractor
    de metadata y el summarizer. 4 caracteres ≈ 1 token; 700 tokens de
    salida por turno. `price_for_turn(turn, kb, has_summary)` devuelve el
    precio (entrada, salida por millón) del tier que tocaría. Ignora los
    cache hits (cota alta)."""
    total = 0.0
    for scenario in config.scenarios:
        for kb in config.attachment_sizes_kb:
            for turn in scenario.turns[: config.max_turns]:
                index = turn.turn_index
                window = min(index - 1, window_turns)
                chars = system_prompt_chars + len(turn.transcript) + window * 1_500
                if index <= window_turns + 1:  # el turno 1 (con su adjunto) aún viaja en la ventana
                    chars += attachment_chars(kb)
                # El resumen aparece al expulsar el primer turno (al guardar el window+1).
                price_in, price_out = price_for_turn(turn, kb, index >= window_turns + 2)
                tokens_in = chars / 4 * 1.5
                total += (tokens_in * price_in + 700 * price_out) / 1_000_000
    return total * config.repeats


def parse_csv_list(value: str, cast: Callable[[str], Any] = str) -> tuple[Any, ...]:
    return tuple(cast(v.strip()) for v in value.split(",") if v.strip())


def select_scenarios(names: Sequence[str], available: dict[str, Scenario]) -> tuple[Scenario, ...]:
    unknown = [n for n in names if n not in available]
    if unknown:
        raise ValueError(f"escenarios desconocidos: {', '.join(unknown)}")
    return tuple(available[n] for n in names)
