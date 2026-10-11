"""Puertos del núcleo conversacional: lo que necesita del exterior.

Los casos de uso (application/) dependen de estos Protocols; las
implementaciones con LLM + prompts viven en adapters/ y en los tests se usan
dobles en memoria. Todos devuelven StructuredCompletion para que el uso
(tokens, coste, latencia) de cada llamada llegue hasta la telemetría.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from app.conversation.domain.anchors import Anchor
from app.conversation.domain.memory import Turn
from app.conversation.domain.metadata import ProjectMetadata
from app.conversation.domain.review import CriticReport
from app.ports.llm import ModelTier, StructuredCompletion
from app.schemas import EstimationResult

MAX_SUMMARY_CHARS = 3000


class ConversationSummary(BaseModel):
    summary: str = Field(
        max_length=MAX_SUMMARY_CHARS,
        description="Resumen acumulado de la conversación expulsada de la ventana.",
    )


@dataclass(frozen=True)
class ConversationSnapshot:
    """Contexto de la conversación que necesitan el critic y el boss: el
    mensaje del turno actual y la memoria a largo plazo (no la ventana)."""

    user_message: str
    project_metadata: ProjectMetadata
    summary: str
    anchors: tuple[Anchor, ...]


class ConversationSummarizer(Protocol):
    def summarize(self, previous_summary: str, evicted: Turn) -> StructuredCompletion[ConversationSummary]: ...


class MetadataExtractorPort(Protocol):
    def extract_with_usage(
        self, current: ProjectMetadata, user_message: str, assistant_message: str
    ) -> StructuredCompletion[ProjectMetadata]: ...


class EstimationCritic(Protocol):
    def review(
        self, snapshot: ConversationSnapshot, estimate: EstimationResult, tier: ModelTier
    ) -> StructuredCompletion[CriticReport]: ...


class EstimationBoss(Protocol):
    def arbitrate(
        self,
        snapshot: ConversationSnapshot,
        estimate: EstimationResult,
        report: CriticReport,
        tier: ModelTier,
    ) -> StructuredCompletion[EstimationResult]: ...
