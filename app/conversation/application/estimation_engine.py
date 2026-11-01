"""Motor de estimación de un turno: actor y, opcionalmente, Actor-Critic-Boss.

- Modo actor (por defecto): una llamada al LLM con los mensajes del turno.
- Modo acb: el critic revisa la estimación del actor; si pide revisión, el
  boss produce la estimación final (siempre con el tier strong). Si el
  critic da ok, no se llama al boss.

Solo el actor puede hacer fallar el turno. Si el critic o el boss fallan
(timeout, proveedor caído, salida inválida), se conserva la estimación del
actor y se registra un warning: la revisión es una mejora, no un requisito.
"""

from dataclasses import dataclass

import structlog

from app.conversation.domain.review import CriticVerdict, EstimationMode
from app.conversation.domain.usage import CallRecord, CallRole
from app.conversation.ports import ConversationSnapshot, EstimationBoss, EstimationCritic
from app.ports.llm import ModelTier, StructuredLLM
from app.schemas import MIN_CONFIDENCE_CONTEXT_KEY, EstimationResult

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ReviewTrace:
    """Qué pasó en la revisión ACB (None en modo actor)."""

    verdict: CriticVerdict | None  # None si el critic falló
    issues: int
    revised: bool  # True si la estimación final es la del boss
    failed_step: str | None = None  # "critic" | "boss"


@dataclass(frozen=True)
class EngineOutcome:
    result: EstimationResult
    calls: tuple[CallRecord, ...]
    review: ReviewTrace | None = None


class EstimationEngine:
    def __init__(
        self,
        llm: StructuredLLM,
        critic: EstimationCritic,
        boss: EstimationBoss,
        *,
        min_confidence_pct: int,
    ) -> None:
        self._llm = llm
        self._critic = critic
        self._boss = boss
        self._min_confidence_pct = min_confidence_pct

    def estimate(
        self,
        messages: list[dict],
        *,
        tier: ModelTier,
        mode: EstimationMode,
        snapshot: ConversationSnapshot,
    ) -> EngineOutcome:
        """Bloqueante (ejecutar en threadpool). Los errores del actor se
        propagan: sin estimación del actor no hay turno."""
        actor = self._llm.complete_structured_with_usage(
            messages,
            EstimationResult,
            context={MIN_CONFIDENCE_CONTEXT_KEY: self._min_confidence_pct},
            tier=tier,
        )
        calls = [CallRecord(CallRole.ACTOR, actor.usage)]
        if mode is EstimationMode.ACTOR:
            return EngineOutcome(actor.parsed, tuple(calls))
        return self._review(actor.parsed, calls, tier=tier, snapshot=snapshot)

    def _review(
        self,
        estimate: EstimationResult,
        calls: list[CallRecord],
        *,
        tier: ModelTier,
        snapshot: ConversationSnapshot,
    ) -> EngineOutcome:
        try:
            critique = self._critic.review(snapshot, estimate, tier)
        except Exception as exc:  # la revisión no puede romper el turno
            log.warning("acb.critic_failed", error_type=type(exc).__name__)
            return EngineOutcome(estimate, tuple(calls), ReviewTrace(None, 0, False, "critic"))

        calls.append(CallRecord(CallRole.CRITIC, critique.usage))
        report = critique.parsed
        if report.verdict is CriticVerdict.OK:
            log.info("acb.reviewed", verdict=report.verdict.value, issues=len(report.issues), revised=False)
            return EngineOutcome(estimate, tuple(calls), ReviewTrace(report.verdict, len(report.issues), False))

        try:
            decision = self._boss.arbitrate(snapshot, estimate, report, ModelTier.STRONG)
        except Exception as exc:
            log.warning("acb.boss_failed", error_type=type(exc).__name__)
            return EngineOutcome(
                estimate, tuple(calls), ReviewTrace(report.verdict, len(report.issues), False, "boss")
            )

        calls.append(CallRecord(CallRole.BOSS, decision.usage))
        log.info("acb.reviewed", verdict=report.verdict.value, issues=len(report.issues), revised=True)
        return EngineOutcome(decision.parsed, tuple(calls), ReviewTrace(report.verdict, len(report.issues), True))
