"""Tipos comunes de los guardrails y registro de políticas.

Cada guardrail declara en código qué pasa cuando dispara (FailurePolicy) y
qué setting controla su modo de despliegue (GuardrailMode). El registro
GUARDRAILS responde de un vistazo a "¿qué pasa cuando este guardrail
dispara?" sin tener que leer el pipeline.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class FailurePolicy(str, Enum):
    # Aborta con un error explícito (4xx). Violaciones graves del input.
    EXCEPTION = "exception"
    # Se pide al modelo que corrija y se reintenta (Instructor al fallar un
    # validador). Errores recuperables del output.
    FIX_RETRY = "fix_retry"
    # Devuelve una respuesta segura por defecto: no se puede cumplir pero no
    # es un error (fuera de alcance, PII redactada en la respuesta).
    FILTER = "filter"


class GuardrailMode(str, Enum):
    # Registra el disparo pero no bloquea. Por defecto para heurísticas
    # nuevas: primero se observa, se ajusta y solo entonces se aplica.
    LOG_ONLY = "log_only"
    # Aplica la política.
    ENFORCE = "enforce"


@dataclass(frozen=True)
class GuardrailResult:
    name: str
    triggered: bool
    policy: FailurePolicy
    score: float | None = None
    # SOLO para logs, nunca para el cliente: categorías o identificadores de
    # patrón que dispararon, nunca el texto ni la PII detectada.
    internal_detail: str | None = None
    # Scores por categoría (moderación), para poder ajustar umbrales.
    scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardrailSpec:
    name: str
    layer: int
    stage: Literal["input", "output"]
    policy: FailurePolicy
    # Atributo de Settings que fija el modo. None = siempre ENFORCE (forma
    # parte del contrato y no tiene sentido desactivarlo).
    mode_setting: str | None
    purpose: str


GUARDRAILS: dict[str, GuardrailSpec] = {
    spec.name: spec
    for spec in (
        GuardrailSpec(
            name="prompt_injection",
            layer=2,
            stage="input",
            policy=FailurePolicy.EXCEPTION,
            mode_setting="guardrail_injection_mode",
            purpose="Patrones de prompt injection sobre el texto normalizado.",
        ),
        GuardrailSpec(
            name="pii_input",
            layer=2,
            stage="input",
            policy=FailurePolicy.EXCEPTION,
            mode_setting="guardrail_pii_input_mode",
            purpose="Emails, teléfonos e IBAN en la descripción.",
        ),
        GuardrailSpec(
            name="moderation",
            layer=2,
            stage="input",
            policy=FailurePolicy.EXCEPTION,
            mode_setting="guardrail_moderation_mode",
            purpose="Contenido tóxico según la Moderation API de OpenAI.",
        ),
        GuardrailSpec(
            name="output_contract",
            layer=4,
            stage="output",
            policy=FailurePolicy.FIX_RETRY,
            mode_setting=None,
            purpose=(
                "Schema y validadores de EstimationResult (coherencia de "
                "totales, confianza mínima, out-of-scope a cero). Lo aplica "
                "Instructor reenviando los errores al modelo."
            ),
        ),
        GuardrailSpec(
            name="out_of_scope",
            layer=5,
            stage="output",
            policy=FailurePolicy.FILTER,
            mode_setting=None,
            purpose="El modelo no puede estimar: respuesta segura con todo a cero.",
        ),
        GuardrailSpec(
            name="pii_output",
            layer=5,
            stage="output",
            policy=FailurePolicy.FILTER,
            mode_setting="guardrail_pii_output_mode",
            purpose="PII en summary, nombres de fase y asunciones: se redacta.",
        ),
    )
}


def mode_for(spec: GuardrailSpec, settings: object) -> GuardrailMode:
    """Modo efectivo de un guardrail según la configuración (Settings)."""
    if spec.mode_setting is None:
        return GuardrailMode.ENFORCE
    return GuardrailMode(getattr(settings, spec.mode_setting))


class GuardrailError(Exception):
    """Error base de los guardrails."""


class InputRejectedError(GuardrailError):
    """Un guardrail de input en ENFORCE con política EXCEPTION ha disparado.
    `guardrail` es solo para logs: nunca se devuelve al cliente."""

    def __init__(self, guardrail: str) -> None:
        super().__init__(guardrail)
        self.guardrail = guardrail


class ModerationUnavailableError(GuardrailError):
    """La Moderation API no respondió (timeout, red o error HTTP)."""
