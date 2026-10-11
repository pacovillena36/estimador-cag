"""CLI de evals: ejecuta el dataset golden contra el LLM real.

    uv run python -m evals.run --mode actor
    uv run python -m evals.run --mode acb --cases web_reservas,oos_cocina --out reporte.json

Gasta dinero: cada caso hace llamadas reales (actor, extractor, summarizer
y, en acb, critic y boss). No forma parte del CI. Usa la configuración del
.env (proveedores, modelos, tier, guardrails).
"""

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.conversation.domain.review import EstimationMode
from app.guardrails.moderation import get_moderation_client
from app.guardrails.pipeline import build_input_pipeline, build_output_pipeline
from app.logging_config import configure_logging
from app.services.llm_gateway import get_llm_gateway
from evals.dataset import DEFAULT_DATASET, load_dataset
from evals.executor import ServiceCaseExecutor
from evals.metrics import default_metrics
from evals.runner import EvalReport, EvalRunner


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evals del estimador conversacional")
    parser.add_argument("--mode", choices=[m.value for m in EstimationMode], default="actor")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--cases", help="ids separados por comas (por defecto, todos)")
    parser.add_argument("--out", type=Path, help="guarda el informe completo en JSON")
    return parser.parse_args(argv)


def _print_report(report: EvalReport) -> None:
    names = report.metric_names
    print(f"\nModo: {report.mode}  ·  casos: {len(report.cases)}\n")
    header = f"{'caso':<28}" + "".join(f"{n:>18}" for n in names) + f"{'coste $':>11}{'lat. ms':>10}  tiers"
    print(header)
    print("-" * len(header))
    for case in report.cases:
        marks = "".join(f"{('ok' if m.passed else 'FALLA'):>18}" for m in case.metrics)
        tiers = ",".join(t.tier for t in case.turns) or (case.error or "-")
        print(f"{case.case_id:<28}{marks}{case.cost_usd:>11.4f}{case.latency_ms:>10.0f}  {tiers}")
    print("-" * len(header))
    rates = "".join(f"{report.pass_rate(n):>18.0%}" for n in names)
    print(f"{'acierto':<28}{rates}{report.total_cost_usd:>11.4f}{report.mean_latency_ms:>10.0f}  (latencia media)")
    print(f"\nCasos que pasan las {len(names)} métricas: {report.pass_rate():.0%}\n")
    for case in report.cases:
        for metric in case.metrics:
            if not metric.passed:
                print(f"  {case.case_id} · {metric.name}: {metric.detail}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.model_copy(update={"log_level": "WARNING"}))

    dataset = load_dataset(args.dataset)
    cases = dataset.cases
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c.id in wanted]
        unknown = wanted - {c.id for c in cases}
        if unknown:
            print(f"Casos desconocidos: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2

    executor = ServiceCaseExecutor(
        settings,
        get_llm_gateway(),
        build_input_pipeline(settings, get_moderation_client()),
        build_output_pipeline(settings),
    )
    report = EvalRunner(executor, default_metrics(settings.min_confidence_pct)).run(
        cases, EstimationMode(args.mode)
    )
    _print_report(report)
    if args.out:
        args.out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Informe guardado en {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
