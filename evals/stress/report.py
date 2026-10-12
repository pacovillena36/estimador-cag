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
from evals.stress.scenarios import TURNS_PER_SCENARIO as TURNS

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
#
# Escritas para un lector no técnico: encabezados en lenguaje funcional y
# una pregunta por tabla. Criterios comunes:
# - Coste y tiempo de espera salen solo de los mensajes que consultaron de
#   verdad a la IA (sin cache hit).
# - La memoria se mide en la primera repetición de cada conversación: las
#   repeticiones con los mismos textos son copias servidas por la caché.

SCENARIO_LABELS = {
    "growing": "Proyecto que crece",
    "pivot": "Cambio de tecnología",
    "contradiction": "Presupuesto corregido",
}
TIER_LABELS = {"fast": "barato (gpt-4o-mini)", "strong": "potente (gpt-4o)"}


def _label(scenario: str) -> str:
    return SCENARIO_LABELS.get(scenario, scenario)


def _scenario_order(scenario: str) -> int:
    order = list(SCENARIO_LABELS)
    return order.index(scenario) if scenario in order else len(order)


def _doc(kb: str) -> str:
    return "sin documento" if kb == "0" else f"{kb}k caracteres"


def _first_repeat(rows: Iterable[Row]) -> list[Row]:
    return [r for r in rows if r["repeat"] == "1"]


def _seconds(ms: float | None) -> str:
    return _fmt(None if ms is None else ms / 1000, "{:.1f} s")


def _yes_no(value: bool | None) -> str:
    return {True: "sí", False: "no", None: "–"}[value]


def setup_section(rows: Sequence[Row], csv_path: Path) -> str:
    ok = _ok(rows)
    real = _no_cache(ok)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=STRESS_DIR, check=False
        ).stdout.strip()
    except OSError:  # sin git instalado (p. ej. en un contenedor)
        commit = ""
    errors = Counter(r["error"] for r in rows if r["error"])
    conversations = {(r["scenario"], r["attachment_kb"], r["repeat"]) for r in rows}
    lines = [
        f"- Tablas generadas el {datetime.now(UTC):%Y-%m-%d} (commit `{commit or '?'}`) a partir de `{csv_path.name}`.",
        f"- {len(conversations)} conversaciones y {len(rows)} mensajes enviados: {len(real)} consultaron de verdad "
        f"a la IA, {len(ok) - len(real)} se sirvieron desde la caché y {len(rows) - len(ok)} fallaron.",
        f"- Gasto total real: {sum(_values(ok, 'cost_usd')):.2f} USD.",
    ]
    if errors:
        lines.append("- Fallos: " + ", ".join(f"{n} × `{e}`" for e, n in errors.most_common()) + ".")
    return "\n".join(lines)


def summary_table(rows: Sequence[Row]) -> str:
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in _first_repeat(rows):
        groups[(row["scenario"], row["attachment_kb"])].append(row)
    body = []
    for (scenario, kb), group in sorted(groups.items(), key=lambda g: (_scenario_order(g[0][0]), int(g[0][1]))):
        ok = _ok(group)
        real = _no_cache(ok)
        latency = _values(real, "latency_ms")
        last = max(ok, key=lambda r: int(r["turn"])) if ok else None
        slow = sum(1 for r in real if r["latency_ok"] == "False")
        sent = max(int(r["turn"]) for r in group)
        body.append(
            [
                _label(scenario),
                _doc(kb),
                f"{len(ok)} de {sent}" + ("" if sent == TURNS else f" (cortada en el {sent})"),
                _fmt(sum(_values(ok, "cost_usd")), "{:.2f} $"),
                _seconds(percentile(latency, 50)),
                _seconds(percentile(latency, 95)),
                f"{slow} de {len(real)}",
                _fmt(_num(last["drift_recall"]) if last else None, "{:.0%}"),
                _yes_no(_bool(last["first_fact_found"]) if last else None),
                _yes_no(_bool(last["stale_fact_present"]) if last else None),
            ]
        )
    header = [
        "Conversación", "Documento", "Mensajes completados", "Coste", "Espera habitual", "Espera en el peor caso",
        "Mensajes de más de 15 s", "Recuerdo al final", "¿Recuerda el nombre?", "¿Arrastra el dato corregido?",
    ]  # fmt: skip
    return _table(header, body)


def latency_vs_tokens(rows: Sequence[Row]) -> str:
    real = _no_cache(_ok(rows))
    body = []
    for low, high in zip(TOKEN_BUCKETS, (*TOKEN_BUCKETS[1:], None), strict=True):
        group = [r for r in real if low <= float(r["tokens_in"]) < (high or float("inf"))]
        if not group:
            continue
        latency = _values(group, "latency_ms")
        label = f"de {low // 1000}k a {high // 1000}k" if high else f"más de {low // 1000}k"
        body.append(
            [
                label,
                str(len(group)),
                _seconds(percentile(latency, 50)),
                _seconds(percentile(latency, 95)),
                _fmt(_mean(_values(group, "cost_usd")), "{:.3f} $"),
            ]
        )
    header = ["Texto que lee la IA (tokens)", "Mensajes", "Espera habitual", "Espera en el peor caso", "Coste medio"]
    return _table(header, body)


def cumulative_cost(rows: Sequence[Row]) -> str:
    real = _no_cache(_ok(rows))
    scenarios = [s for s in SCENARIO_LABELS if any(r["scenario"] == s for r in real)]
    by_turn: dict[tuple[str, int], list[Row]] = defaultdict(list)
    for row in real:
        by_turn[(row["scenario"], int(row["turn"]))].append(row)
    running = dict.fromkeys(scenarios, 0.0)
    body = []
    for turn in range(1, TURNS + 1):
        cells = [str(turn)]
        for scenario in scenarios:
            group = by_turn.get((scenario, turn), [])
            mean = _mean(_values(group, "cost_usd"))
            if mean is not None:
                running[scenario] += mean
            strong = _rate(group, lambda r: r["last_resolved_tier"] == "strong")
            model = "" if strong is None else (" · potente" if strong >= 0.5 else " · barato")
            cells.append(f"{_fmt(mean, '{:.3f}')} → {running[scenario]:.2f}{model}")
        body.append(cells)
    return _table(["Mensaje", *(_label(s) for s in scenarios)], body)


def by_tier(rows: Sequence[Row]) -> str:
    real = _no_cache(_ok(rows))
    body = []
    for tier in ("fast", "strong"):
        group = [r for r in real if r["last_resolved_tier"] == tier]
        if not group:
            continue
        body.append(
            [
                TIER_LABELS[tier],
                str(len(group)),
                _fmt(_mean(_values(group, "cost_usd")), "{:.3f} $"),
                _seconds(percentile(_values(group, "latency_ms"), 50)),
                _fmt(_rate(group, lambda r: r["cost_ok"] == "True"), "{:.0%}"),
            ]
        )
    header = ["Modelo de IA", "Mensajes", "Coste medio por mensaje", "Espera habitual", "Dentro del presupuesto de coste"]
    return _table(header, body)


def drift_by_checkpoint(rows: Sequence[Row]) -> str:
    ok = _ok(_first_repeat(rows))
    body = []
    for scenario in SCENARIO_LABELS:
        for n in CHECKPOINTS:
            group = [r for r in ok if r["scenario"] == scenario and int(r["turn"]) == n]
            if not group:
                continue
            first = [b for r in group if (b := _bool(r["first_fact_found"])) is not None]
            stale = [b for r in group if (b := _bool(r["stale_fact_present"])) is not None]
            body.append(
                [
                    _label(scenario),
                    str(n),
                    group[0]["facts_expected"],
                    _fmt(_mean(_values(group, "drift_recall")), "{:.0%}"),
                    _fmt(sum(first) / len(first) if first else None, "{:.0%}"),
                    _fmt(sum(stale) / len(stale) if stale else None, "{:.0%}"),
                    str(len(group)),
                ]
            )
    header = [
        "Conversación", "Mensaje", "Datos a recordar", "Recuerdo medio", "Recuerda el nombre",
        "Arrastra el dato corregido", "Conversaciones",
    ]  # fmt: skip
    return _table(header, body)


def canary_recall(rows: Sequence[Row]) -> str:
    ok = [r for r in _ok(_first_repeat(rows)) if r["attachment_kb"] != "0"]
    body = []
    for kb in _ordered((r["attachment_kb"] for r in ok), key=int):
        group = [r for r in ok if r["attachment_kb"] == kb]
        first = [r for r in group if r["turn"] == "1"]
        scenarios = {r["scenario"] for r in group}
        finals = [max((r for r in group if r["scenario"] == s), key=lambda r: int(r["turn"])) for s in scenarios]
        sent = _mean(_values(first, "attachment_canaries_sent"))
        body.append(
            [
                _doc(kb),
                _fmt(_mean(_values(first, "attachments_total_chars"))),
                f"{_fmt(sent)} de {group[0]['attachment_canaries_total']}",
                _fmt(_mean(_values(finals, "attachment_recall")), "{:.0%}"),
            ]
        )
    header = ["Documento adjunto", "Caracteres que lee la IA", "Marcas que llegan a la IA", "Marcas que recuerda al final"]
    return _table(header, body)


def cache_effect(rows: Sequence[Row]) -> str:
    ok = _ok(rows)
    body = []
    for kind, label in (("none", "Consulta real a la IA"), ("exact", "Respuesta reutilizada (caché)")):
        group = [r for r in ok if r["cache_hit_kind"] == kind]
        if group:
            body.append(
                [
                    label,
                    str(len(group)),
                    _seconds(percentile(_values(group, "latency_ms"), 50)),
                    _fmt(_mean(_values(group, "cost_usd")), "{:.3f} $"),
                ]
            )
    return _table(["Tipo de respuesta", "Mensajes", "Espera habitual", "Coste medio"], body)


def render(rows: Sequence[Row], csv_path: Path) -> str:
    sections = [
        ("Datos de la ejecución", setup_section(rows, csv_path)),
        ("Resumen por conversación", summary_table(rows)),
        ("¿Cuánto cuesta cada mensaje? (coste del mensaje → acumulado, en USD)", cumulative_cost(rows)),
        ("Modelo barato frente a modelo potente", by_tier(rows)),
        ("¿Cuánto se espera según el texto que lee la IA?", latency_vs_tokens(rows)),
        ("¿Cuánto recuerda a medida que avanza la conversación?", drift_by_checkpoint(rows)),
        ("¿Qué pasa con los documentos adjuntos?", canary_recall(rows)),
        ("Efecto de la caché", cache_effect(rows)),
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
