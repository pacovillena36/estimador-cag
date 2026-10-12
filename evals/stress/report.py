"""Genera las tablas de evals/stress/REPORT.md a partir de results.csv.

    uv run python -m evals.stress.report [--csv evals/stress/results.csv]

Solo stdlib (csv, statistics). Lee únicamente el CSV y reemplaza el bloque
entre los marcadores BEGIN/END de REPORT.md; el resto del reporte (setup y
lectura de resultados) se redacta a mano sobre estos números.
"""

import argparse
import csv
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from evals.stress.scenarios import CHECKPOINTS

STRESS_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = STRESS_DIR / "results.csv"
REPORT = STRESS_DIR / "REPORT.md"
BEGIN = "<!-- BEGIN:generated (evals.stress.report) -->"
END = "<!-- END:generated -->"
TOKEN_BUCKETS: tuple[int, ...] = (0, 2_000, 4_000, 8_000, 16_000, 32_000)

Row = dict[str, str]


# ------------------------------------------------------------- utilidades


def _num(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def _bool(value: str) -> bool | None:
    return {"True": True, "False": False}.get(value)


def _values(rows: Iterable[Row], column: str) -> list[float]:
    return [v for r in rows if (v := _num(r[column])) is not None]


def percentile(values: Sequence[float], pct: int) -> float | None:
    """Percentil con interpolación (método inclusive de statistics)."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[pct - 1]


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _fmt(value: float | None, pattern: str = "{:.0f}") -> str:
    return "–" if value is None else pattern.format(value)


def _rate(rows: Sequence[Row], predicate: Callable[[Row], bool]) -> float | None:
    return sum(map(predicate, rows)) / len(rows) if rows else None


def _table(header: Sequence[str], body: Iterable[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---:" if i else "---" for i in range(len(header))) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def _ok(rows: Iterable[Row]) -> list[Row]:
    return [r for r in rows if r["turn_index"] != ""]


def _no_cache(rows: Iterable[Row]) -> list[Row]:
    return [r for r in rows if r["cache_hit_kind"] == "none"]


def _ordered(values: Iterable[str], key: Callable[[str], object] = str) -> list[str]:
    return sorted(set(values), key=key)


# ----------------------------------------------------------------- tablas


def setup_section(rows: Sequence[Row], csv_path: Path) -> str:
    ok = _ok(rows)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=STRESS_DIR, check=False
    ).stdout.strip()
    tiers = Counter(r["last_resolved_tier"] for r in ok)
    errors = Counter(r["error"] for r in rows if r["error"])
    conversations = {(r["scenario"], r["attachment_kb"], r["repeat"]) for r in rows}
    lines = [
        f"- Tablas generadas: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · commit `{commit or '?'}` · CSV `{csv_path.name}`",
        f"- Filas: {len(rows)} ({len(ok)} turnos completados, {len(rows) - len(ok)} fallidos) en {len(conversations)} conversaciones",
        f"- Turnos por tier: {dict(tiers)}",
        f"- Coste total observado: {sum(_values(ok, 'cost_usd')):.4f} USD",
    ]
    if errors:
        lines.append("- Errores: " + ", ".join(f"`{e}` x{n}" for e, n in errors.most_common()))
    return "\n".join(lines)


def summary_table(rows: Sequence[Row]) -> str:
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in _ok(rows):
        groups[(row["scenario"], row["attachment_kb"])].append(row)
    body = []
    for (scenario, kb), group in sorted(groups.items(), key=lambda g: (g[0][0], int(g[0][1]))):
        latency, latency_nc = _values(group, "latency_ms"), _values(_no_cache(group), "latency_ms")
        body.append(
            [
                scenario,
                kb,
                str(len(group)),
                _fmt(percentile(latency, 50)),
                _fmt(percentile(latency, 95)),
                _fmt(percentile(latency_nc, 50)),
                _fmt(percentile(latency_nc, 95)),
                _fmt(sum(_values(group, "cost_usd")), "{:.4f}"),
                _fmt(_rate(group, lambda r: r["cache_hit_kind"] == "exact"), "{:.0%}"),
                _fmt(_rate(group, lambda r: r["cache_hit_kind"] == "semantic"), "{:.0%}"),
                _fmt(_mean(_values(group, "drift_recall")), "{:.0%}"),
            ]
        )
    header = [
        "escenario", "adjunto KB", "turnos", "P50 ms", "P95 ms", "P50 ms sin caché", "P95 ms sin caché",
        "coste USD", "exact hit", "semantic hit", "recall medio",
    ]  # fmt: skip
    return _table(header, body)


def latency_vs_tokens(rows: Sequence[Row]) -> str:
    rows = _no_cache(_ok(rows))
    body = []
    for low, high in zip(TOKEN_BUCKETS, (*TOKEN_BUCKETS[1:], None), strict=True):
        group = [r for r in rows if low <= float(r["tokens_in"]) < (high or float("inf"))]
        if not group:
            continue
        latency = _values(group, "latency_ms")
        label = f"{low // 1000}k–{high // 1000}k" if high else f">= {low // 1000}k"
        body.append(
            [
                label,
                str(len(group)),
                _fmt(_mean(_values(group, "tokens_in"))),
                _fmt(percentile(latency, 50)),
                _fmt(percentile(latency, 95)),
                _fmt(_mean(_values(group, "cost_usd")), "{:.5f}"),
            ]
        )
    return _table(["tokens_in (turno)", "turnos", "tokens_in medio", "P50 ms", "P95 ms", "coste medio USD"], body)


def cumulative_cost(rows: Sequence[Row]) -> str:
    # Sin cache hits: un hit cuesta 0 y promediarlo mezclaría el efecto
    # caché con el coste del CAG.
    ok = _no_cache(_ok(rows))
    scenarios = _ordered(r["scenario"] for r in ok)
    by_turn: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in ok:
        by_turn[(row["scenario"], int(row["turn"]))].append(float(row["cost_usd"]))
    turns = _ordered((r["turn"] for r in ok), key=int)
    running = dict.fromkeys(scenarios, 0.0)
    body = []
    for turn in turns:
        cells = [turn]
        for scenario in scenarios:
            mean = _mean(by_turn.get((scenario, int(turn)), []))
            if mean is not None:
                running[scenario] += mean
            tiers = Counter(r["last_resolved_tier"] for r in ok if r["scenario"] == scenario and r["turn"] == turn)
            tier = f" [{tiers.most_common(1)[0][0]}]" if tiers else ""
            cells.append(f"{_fmt(mean, '{:.5f}')} / {running[scenario]:.4f}{tier}")
        body.append(cells)
    note = (
        "Cada celda: coste medio del turno / acumulado de esas medias (USD), solo turnos sin cache hit, "
        "todos los tamaños de adjunto; entre corchetes, el tier mayoritario del turno."
    )
    return note + "\n\n" + _table(["turno", *scenarios], body)


def by_tier(rows: Sequence[Row]) -> str:
    rows = _no_cache(_ok(rows))
    body = []
    for tier in _ordered(r["last_resolved_tier"] for r in rows):
        group = [r for r in rows if r["last_resolved_tier"] == tier]
        latency = _values(group, "latency_ms")
        body.append(
            [
                tier,
                str(len(group)),
                _fmt(_mean(_values(group, "tokens_in"))),
                _fmt(_mean(_values(group, "tokens_out"))),
                _fmt(_mean(_values(group, "cost_usd")), "{:.5f}"),
                _fmt(percentile(latency, 50)),
                _fmt(_rate(group, lambda r: r["cost_ok"] == "True"), "{:.0%}"),
                _fmt(_rate(group, lambda r: r["latency_ok"] == "True"), "{:.0%}"),
            ]
        )
    header = ["tier", "turnos", "tokens_in medio", "tokens_out medio", "coste medio USD", "P50 ms", "cost_ok", "latency_ok"]
    return _table(header, body)


def drift_by_checkpoint(rows: Sequence[Row]) -> str:
    ok = _ok(rows)
    body = []
    for scenario in _ordered(r["scenario"] for r in ok):
        for n in CHECKPOINTS:
            group = [r for r in ok if r["scenario"] == scenario and int(r["turn"]) == n]
            if not group:
                continue
            first = [b for r in group if (b := _bool(r["first_fact_found"])) is not None]
            stale = [b for r in group if (b := _bool(r["stale_fact_present"])) is not None]
            body.append(
                [
                    scenario,
                    str(n),
                    str(len(group)),
                    group[0]["facts_expected"],
                    _fmt(_mean(_values(group, "drift_recall")), "{:.0%}"),
                    _fmt(sum(first) / len(first) if first else None, "{:.0%}"),
                    _fmt(sum(stale) / len(stale) if stale else None, "{:.0%}"),
                    _fmt(_mean(_values(group, "summary_chars"))),
                    _fmt(_mean(_values(group, "anchors_count")), "{:.1f}"),
                ]
            )
    header = ["escenario", "N", "muestras", "facts", "recall", "fact turno 1", "stale presente", "summary chars", "anclas"]
    return _table(header, body)


def canary_recall(rows: Sequence[Row]) -> str:
    ok = [r for r in _ok(rows) if r["attachment_kb"] != "0"]
    body = []
    for kb in _ordered((r["attachment_kb"] for r in ok), key=int):
        group = [r for r in ok if r["attachment_kb"] == kb]
        first = [r for r in group if r["turn"] == "1"]
        last_turn = max(int(r["turn"]) for r in group)
        last = [r for r in group if int(r["turn"]) == last_turn]
        body.append(
            [
                kb,
                _fmt(_mean(_values(first, "attachments_total_chars"))),
                group[0]["attachment_canaries_total"],
                _fmt(_mean(_values(first, "attachment_canaries_sent")), "{:.1f}"),
                _fmt(_mean(_values(first, "attachment_recall")), "{:.0%}"),
                f"{_fmt(_mean(_values(last, 'attachment_recall')), '{:.0%}')} (turno {last_turn})",
                _fmt(_mean(_values(first, "latency_ms"))),
                ", ".join(f"{t}:{n}" for t, n in Counter(r["last_resolved_tier"] for r in first).items()),
            ]
        )
    header = [
        "adjunto KB", "chars enviados (turno 1)", "canarios", "canarios enviados", "recall turno 1",
        "recall final", "latencia turno 1 ms", "tier turno 1",
    ]  # fmt: skip
    return _table(header, body)


def render(rows: Sequence[Row], csv_path: Path) -> str:
    sections = [
        ("Setup de la ejecución", setup_section(rows, csv_path)),
        ("Tabla resumen (escenario × adjunto)", summary_table(rows)),
        ("Curva 1 · latency_ms vs tokens_in (sin cache hits)", latency_vs_tokens(rows)),
        ("Curva 2 · cost_usd acumulado vs turn_index", cumulative_cost(rows)),
        ("Coste y latencia por tier (sin cache hits)", by_tier(rows)),
        ("Curva 3 · MemoryDriftMetric (recall) vs N", drift_by_checkpoint(rows)),
        ("Recall de canarios del adjunto (truncado)", canary_recall(rows)),
    ]
    return "\n\n".join(f"### {title}\n\n{body}" for title, body in sections)


def write_report(generated: str, report: Path = REPORT) -> None:
    block = f"{BEGIN}\n\n{generated}\n\n{END}"
    text = report.read_text(encoding="utf-8") if report.exists() else f"# Stress test del CAG\n\n{BEGIN}\n{END}\n"
    if BEGIN not in text or END not in text:
        raise ValueError(f"{report} no tiene los marcadores {BEGIN} / {END}")
    head, rest = text.split(BEGIN, 1)
    tail = rest.split(END, 1)[1]
    report.write_text(head + block + tail, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenera las tablas de REPORT.md desde el CSV")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args(argv)
    if not args.csv.exists():
        print(f"ERROR: no existe {args.csv}", file=sys.stderr)
        return 2
    with args.csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        print("ERROR: el CSV no tiene filas", file=sys.stderr)
        return 2
    write_report(render(rows, args.csv))
    print(f"Tablas regeneradas en {REPORT} ({len(rows)} filas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
