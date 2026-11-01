"""CLI del stress test del CAG: escenarios x adjuntos x repeticiones -> CSV.

    uv run python -m evals.stress.run --http http://localhost:8000 --dry-run
    uv run python -m evals.stress.run --http http://localhost:8000 --max-turns 3 --repeats 1
    uv run python -m evals.stress.run --http http://localhost:8000 \\
        --scenarios growing,pivot,contradiction --attachment-sizes 0,5,20,50,100 \\
        --repeats 3 --output evals/stress/results.csv

Sin --http, la app corre en el mismo proceso (httpx.ASGITransport). Gasta
dinero: cada turno hace llamadas reales al LLM. La API debe arrancar con
EXPOSE_TURN_OBSERVATION=true y EXPOSE_SESSION_MEMORY_DEBUG=true (en modo
in-process se activan solos). Secuencial: las latencias deben ser
comparables entre turnos.
"""

import argparse
import asyncio
import csv
import sys
from collections import Counter
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from urllib.parse import urlparse

from app.config import get_settings
from app.conversation.domain.metadata import ProjectMetadata
from app.conversation.domain.tiering import TierSignals
from app.dependencies import build_tier_policy
from app.ports.llm import ModelTier
from app.prompts.loader import render_session_system_prompt
from app.schemas import DetailLevel, OutputFormat, ProjectType
from app.services.llm_pricing import price_for
from evals.stress.client import SessionApiClient, http_client, in_process_client
from evals.stress.fixtures.build_pdfs import OUT_DIR, load_manifest
from evals.stress.runner import (
    CSV_COLUMNS,
    SetupError,
    StressConfig,
    StressRunner,
    estimate_cost_usd,
    parse_csv_list,
    select_scenarios,
)
from evals.stress.scenarios import SCENARIOS, TURNS_PER_SCENARIO, Turn

STRESS_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = STRESS_DIR / "results.csv"
# Presupuestos por turno (documentados en REPORT.md).
DEFAULT_LATENCY_BUDGET_MS = 15_000.0
DEFAULT_COST_BUDGET_USD = 0.01
DEFAULT_MAX_TOTAL_COST_USD = 5.0
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress test del CAG (latencia, coste y memoria)")
    parser.add_argument("--http", metavar="URL", help="API a la que llamar; sin él, la app corre in-process")
    parser.add_argument("--allow-remote", action="store_true", help="permite --http a un host que no es localhost")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--attachment-sizes", default="0,5,20,50,100", help="KB de texto del adjunto (0 = sin adjunto)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=TURNS_PER_SCENARIO)
    parser.add_argument("--latency-budget-ms", type=float, default=DEFAULT_LATENCY_BUDGET_MS)
    parser.add_argument("--cost-budget-usd", type=float, default=DEFAULT_COST_BUDGET_USD)
    parser.add_argument("--max-total-cost-usd", type=float, default=DEFAULT_MAX_TOTAL_COST_USD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="solo imprime llamadas y coste estimados")
    return parser.parse_args(argv)


def _validate_output(path: Path) -> Path:
    """Solo se escribe dentro de evals/stress/ y en .csv."""
    resolved = (path if path.is_absolute() else Path.cwd() / path).resolve()
    if not resolved.is_relative_to(STRESS_DIR) or resolved.suffix != ".csv":
        raise ValueError(f"--output debe ser un .csv dentro de {STRESS_DIR}")
    return resolved


def _validate_url(url: str, allow_remote: bool) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"--http inválido: {url!r}")
    if parsed.hostname not in _LOCAL_HOSTS and not allow_remote:
        raise ValueError(f"{parsed.hostname} no es localhost: añade --allow-remote si de verdad quieres atacarlo")
    return url


def _build_config(args: argparse.Namespace) -> StressConfig:
    sizes = parse_csv_list(args.attachment_sizes, int)
    invalid = [kb for kb in sizes if kb not in (0, 5, 20, 50, 100)]
    if invalid:
        raise ValueError(f"tamaños de adjunto no soportados: {invalid} (0, 5, 20, 50, 100)")
    if args.repeats < 1 or not 1 <= args.max_turns <= TURNS_PER_SCENARIO:
        raise ValueError(f"--repeats >= 1 y --max-turns entre 1 y {TURNS_PER_SCENARIO}")
    if args.latency_budget_ms <= 0 or args.cost_budget_usd < 0 or args.max_total_cost_usd <= 0:
        raise ValueError("presupuestos inválidos (latencia > 0, coste >= 0, coste total > 0)")
    return StressConfig(
        scenarios=select_scenarios(parse_csv_list(args.scenarios), SCENARIOS),
        attachment_sizes_kb=sizes,
        repeats=args.repeats,
        max_turns=args.max_turns,
        latency_budget_ms=args.latency_budget_ms,
        cost_budget_usd=args.cost_budget_usd,
        max_total_cost_usd=args.max_total_cost_usd,
    )


def _dry_run(config: StressConfig) -> None:
    settings = get_settings()
    openai = settings.llm_provider == "openai"
    models = {
        ModelTier.FAST: settings.openai_model if openai else settings.anthropic_model,
        ModelTier.STRONG: settings.openai_strong_model if openai else settings.anthropic_strong_model,
    }
    prices = {tier: price_for(model) for tier, model in models.items()}
    system_chars = len(
        render_session_system_prompt(
            project_type=ProjectType.WEB_SAAS,
            detail_level=DetailLevel.MEDIUM,
            output_format=OutputFormat.PHASES_TABLE,
            project_metadata=ProjectMetadata(),
            version=settings.session_prompt_version,
            min_confidence_pct=settings.min_confidence_pct,
        )
    )
    cap = min(settings.max_attachment_chars, settings.max_total_attachment_chars)
    policy = build_tier_policy(settings)
    tiers: Counter[str] = Counter()

    def attachment_chars(kb: int) -> int:
        return min(kb * 1000, cap)

    def price_for_turn(turn: Turn, kb: int, has_summary: bool) -> tuple[float, float]:
        # Mismas reglas que la app; la confianza previa no se conoce de antemano.
        signals = TierSignals(
            transcript=turn.transcript,
            attachment_chars=attachment_chars(kb) if turn.turn_index == 1 else 0,
            previous_confidence_pct=None,
            has_summary=has_summary,
        )
        tier = policy.resolve(signals).tier
        tiers[tier.value] += config.repeats
        price = prices[tier]
        assert price is not None
        return price.input_per_mtok, price.output_per_mtok

    print(f"Llamadas a POST /estimate previstas: {config.planned_turns}")
    print(
        f"  = {len(config.scenarios)} escenarios x {len(config.attachment_sizes_kb)} tamaños x "
        f"{config.repeats} repeticiones x {config.max_turns} turnos"
    )
    print(
        f"Modelos: fast={settings.llm_provider}/{models[ModelTier.FAST]} · "
        f"strong={settings.llm_provider}/{models[ModelTier.STRONG]} · TIER_MODE={settings.tier_mode} · "
        f"ventana={settings.max_conversation_turns}"
    )
    if None in prices.values():
        print("Coste estimado: desconocido (algún modelo no está en MODEL_COSTS)")
        return
    cost = estimate_cost_usd(
        config,
        price_for_turn=price_for_turn,
        system_prompt_chars=system_chars,
        attachment_chars=attachment_chars,
        window_turns=settings.max_conversation_turns,
    )
    print(f"Turnos por tier (reglas de TierPolicy): {dict(tiers)}")
    print(
        f"Coste estimado (aprox., sin cache hits ni ACB): ~{cost:.2f} USD "
        f"(límite --max-total-cost-usd {config.max_total_cost_usd:g} USD)"
    )


async def _run(config: StressConfig, client_cm: AbstractAsyncContextManager[SessionApiClient], output: Path) -> int:
    fixtures = load_manifest(OUT_DIR) if any(config.attachment_sizes_kb) else {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        handle.flush()

        def sink(row: dict) -> None:
            writer.writerow(row)
            handle.flush()

        async with client_cm as client:
            runner = StressRunner(client, config, fixtures, OUT_DIR, sink, progress=lambda m: print(m, flush=True))
            try:
                summary = await runner.run()
            except SetupError as exc:
                print(f"ERROR de configuración: {exc}", file=sys.stderr)
                return 2
    print(
        f"\nFilas: {summary.rows} · turnos fallidos: {summary.failed_turns} · "
        f"coste observado: {summary.total_cost_usd:.4f} USD · CSV: {output}"
    )
    if summary.aborted:
        print(f"ABORTADO: {summary.aborted}", file=sys.stderr)
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = _build_config(args)
        output = _validate_output(args.output)
        url = _validate_url(args.http, args.allow_remote) if args.http else None
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        _dry_run(config)
        return 0
    if url is None:
        from app.logging_config import configure_logging

        configure_logging(get_settings().model_copy(update={"log_level": "WARNING"}))
    client_cm = http_client(url) if url else in_process_client()
    return asyncio.run(_run(config, client_cm, output))


if __name__ == "__main__":
    raise SystemExit(main())
