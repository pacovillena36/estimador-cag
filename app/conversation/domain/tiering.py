"""Tier dinámico: qué nivel de modelo merece cada turno.

Reglas deterministas evaluadas en orden; gana la primera que se cumple y su
nombre queda registrado (last_tier_rule), así cada decisión es explicable.
El tier es abstracto (fast/strong): qué modelo hay detrás lo decide el
adaptador del LLM.
"""

import re
from dataclasses import dataclass

from app.ports.llm import ModelTier

_COMPLEXITY = re.compile(
    r"integraci[oó]n|integraciones|migraci[oó]n|migrar|legacy|alta disponibilidad|"
    r"tiempo real|real[- ]time|microservicios|multi-?tenant|cumplimiento normativo|"
    r"\bgdpr\b|\brgpd\b|\bhipaa\b|\bpci\b|machine learning|blockchain",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TierSignals:
    """Lo que se sabe del turno antes de llamar al LLM."""

    transcript: str
    attachment_chars: int
    previous_confidence_pct: int | None
    has_summary: bool


@dataclass(frozen=True)
class TierDecision:
    tier: ModelTier
    rule: str


@dataclass(frozen=True)
class TierPolicy:
    enabled: bool = True
    attachment_chars_threshold: int = 8_000
    transcript_chars_threshold: int = 6_000
    low_confidence_pct: int = 60

    def resolve(self, signals: TierSignals) -> TierDecision:
        if not self.enabled:
            return TierDecision(ModelTier.FAST, "tier_mode_off")
        if signals.attachment_chars >= self.attachment_chars_threshold:
            return TierDecision(ModelTier.STRONG, "large_attachments")
        if len(signals.transcript) >= self.transcript_chars_threshold:
            return TierDecision(ModelTier.STRONG, "long_transcript")
        if (
            signals.previous_confidence_pct is not None
            and signals.previous_confidence_pct < self.low_confidence_pct
        ):
            return TierDecision(ModelTier.STRONG, "low_previous_confidence")
        if _COMPLEXITY.search(signals.transcript):
            return TierDecision(ModelTier.STRONG, "complexity_keywords")
        if signals.has_summary:
            return TierDecision(ModelTier.STRONG, "long_conversation")
        return TierDecision(ModelTier.FAST, "default")
