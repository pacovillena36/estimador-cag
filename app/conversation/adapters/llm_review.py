"""Critic y Boss de Actor-Critic-Boss con el LLM (implementan EstimationCritic
y EstimationBoss). Cada uno renderiza su prompt (app/prompts/review/) y pide
una salida estructurada: CriticReport el critic, EstimationResult el boss
(con el mismo contexto de validación que el actor)."""

from app.conversation.domain.review import CriticReport
from app.conversation.ports import ConversationSnapshot
from app.ports.llm import ModelTier, StructuredCompletion, StructuredLLM
from app.prompts.loader import render_boss_prompt, render_critic_prompt
from app.schemas import MIN_CONFIDENCE_CONTEXT_KEY, EstimationResult


def _messages(system: str, user: str) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class LLMCritic:
    def __init__(self, llm: StructuredLLM, *, min_confidence_pct: int, prompt_version: str = "v1") -> None:
        self._llm = llm
        self._min_confidence_pct = min_confidence_pct
        self._prompt_version = prompt_version

    def review(
        self, snapshot: ConversationSnapshot, estimate: EstimationResult, tier: ModelTier
    ) -> StructuredCompletion[CriticReport]:
        system, user = render_critic_prompt(
            user_message=snapshot.user_message,
            project_metadata=snapshot.project_metadata,
            summary=snapshot.summary,
            anchors=snapshot.anchors,
            estimate_json=estimate.model_dump_json(indent=2),
            min_confidence_pct=self._min_confidence_pct,
            version=self._prompt_version,
        )
        return self._llm.complete_structured_with_usage(_messages(system, user), CriticReport, tier=tier)


class LLMBoss:
    def __init__(self, llm: StructuredLLM, *, min_confidence_pct: int, prompt_version: str = "v1") -> None:
        self._llm = llm
        self._min_confidence_pct = min_confidence_pct
        self._prompt_version = prompt_version

    def arbitrate(
        self,
        snapshot: ConversationSnapshot,
        estimate: EstimationResult,
        report: CriticReport,
        tier: ModelTier,
    ) -> StructuredCompletion[EstimationResult]:
        system, user = render_boss_prompt(
            user_message=snapshot.user_message,
            project_metadata=snapshot.project_metadata,
            summary=snapshot.summary,
            anchors=snapshot.anchors,
            estimate_json=estimate.model_dump_json(indent=2),
            critic_report_json=report.model_dump_json(indent=2),
            min_confidence_pct=self._min_confidence_pct,
            version=self._prompt_version,
        )
        return self._llm.complete_structured_with_usage(
            _messages(system, user),
            EstimationResult,
            context={MIN_CONFIDENCE_CONTEXT_KEY: self._min_confidence_pct},
            tier=tier,
        )
