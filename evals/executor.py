"""Adaptador de CaseExecutor con el servicio real de sesiones.

Cada caso corre en una sesión nueva (con su ventana si el caso la fija) y
sus turnos pasan por el mismo camino que POST /sessions/{id}/estimate:
guardrails, tier, memoria, actor o ACB. Sin HTTP ni adjuntos.
"""

import asyncio

from app.config import Settings
from app.conversation.domain.review import EstimationMode
from app.dependencies import build_session_estimation_service, build_session_store
from app.guardrails.pipeline import InputPipeline, OutputPipeline
from app.ports.llm import StructuredLLM
from app.schemas import DetailLevel, OutputFormat
from app.services.session_estimation import TurnInput, TurnOutcome
from evals.dataset import GoldenCase
from evals.runner import CaseRun, TurnObservation


def _observe(outcome: TurnOutcome) -> TurnObservation:
    usage = outcome.usage
    return TurnObservation(
        tier=outcome.tier.tier.value,
        tier_rule=outcome.tier.rule,
        cost_usd=usage.cost_usd,
        latency_ms=usage.latency_ms,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        calls=usage.count_by_role(),
        revised=outcome.engine.review.revised if outcome.engine.review else None,
    )


class ServiceCaseExecutor:
    def __init__(
        self,
        settings: Settings,
        llm: StructuredLLM,
        input_pipeline: InputPipeline,
        output_pipeline: OutputPipeline,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._input = input_pipeline
        self._output = output_pipeline

    def run(self, case: GoldenCase, mode: EstimationMode) -> CaseRun:
        return asyncio.run(self._run(case, mode))

    async def _run(self, case: GoldenCase, mode: EstimationMode) -> CaseRun:
        settings = self._settings
        if case.window is not None:
            settings = settings.model_copy(update={"max_conversation_turns": case.window})
        service = build_session_estimation_service(settings, self._llm, self._input, self._output)
        session = build_session_store(settings).create()

        observations: list[TurnObservation] = []
        outcome: TurnOutcome | None = None
        for transcript in case.turns:
            turn = TurnInput(transcript, case.project_type, DetailLevel.MEDIUM, OutputFormat.PHASES_TABLE, mode)
            try:
                outcome = await service.estimate_turn(session, turn, [])
            except Exception as exc:  # un caso fallido no detiene la eval
                return CaseRun(None, tuple(observations), type(exc).__name__)
            observations.append(_observe(outcome))
        return CaseRun(outcome.response.result if outcome else None, tuple(observations))
