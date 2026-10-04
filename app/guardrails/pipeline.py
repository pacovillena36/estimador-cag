"""Orquestación de los guardrails de input y de output.

Cada guardrail evaluado deja un log `guardrail.evaluated` con request_id,
guardrail, triggered, mode, policy, score, latency_ms y, como mucho, la
categoría o patrón que disparó. Nunca la descripción ni la PII: para
correlacionar peticiones se usa `description_sha256` (hash truncado,
enlazado al contexto de logs por el servicio).

Los guardrails se ejecutan en orden de coste: primero las heurísticas
locales (microsegundos) y al final la Moderation API (red). Un guardrail en
ENFORCE con política EXCEPTION que dispara corta el pipeline.
"""

import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import structlog

from app.guardrails.base import (
    GUARDRAILS,
    FailurePolicy,
    GuardrailMode,
    GuardrailResult,
    GuardrailSpec,
    InputRejectedError,
    ModerationUnavailableError,
    mode_for,
)
from app.guardrails.injection import detect_prompt_injection
from app.guardrails.moderation import ModerationClient
from app.guardrails.normalize import normalize
from app.guardrails.pii import detect_pii, redact
from app.schemas import EstimationResult

log = structlog.get_logger(__name__)


def description_hash(text: str) -> str:
    """SHA-256 truncado: correlaciona logs sin guardar el texto."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _log_result(spec: GuardrailSpec, mode: GuardrailMode, result: GuardrailResult, latency_ms: float) -> None:
    fields = {
        "guardrail": spec.name,
        "layer": spec.layer,
        "triggered": result.triggered,
        "mode": mode.value,
        "policy": spec.policy.value,
        "score": result.score,
        "detail": result.internal_detail,
        "latency_ms": latency_ms,
    }
    if result.scores:
        fields["scores"] = dict(result.scores)
    if result.triggered:
        log.warning("guardrail.evaluated", **fields)
    else:
        log.info("guardrail.evaluated", **fields)


# ------------------------------------------------------------------ input


@dataclass(frozen=True)
class InputGuardrail:
    spec: GuardrailSpec
    check: Callable[[str], GuardrailResult]
    # Las heurísticas trabajan sobre el texto normalizado; la moderación,
    # sobre el original (el modelo de moderación entiende el texto tal cual).
    on_normalized_text: bool = True


class InputPipeline:
    def __init__(
        self,
        guardrails: Sequence[InputGuardrail],
        modes: Mapping[str, GuardrailMode],
        *,
        moderation_fail_closed: bool,
    ) -> None:
        self._guardrails = guardrails
        self._modes = modes
        self._fail_closed = moderation_fail_closed

    def run(self, description: str) -> None:
        """Lanza InputRejectedError si un guardrail en ENFORCE con política
        EXCEPTION dispara, o ModerationUnavailableError si la moderación no
        responde y la política es fail-closed."""
        layer_start = time.perf_counter()
        normalized = normalize(description)
        try:
            for guardrail in self._guardrails:
                self._evaluate(guardrail, normalized if guardrail.on_normalized_text else description)
        finally:
            log.info("guardrail.layer_completed", stage="input", latency_ms=_elapsed_ms(layer_start))

    def _evaluate(self, guardrail: InputGuardrail, text: str) -> None:
        spec = guardrail.spec
        mode = self._modes[spec.name]
        start = time.perf_counter()
        try:
            result = guardrail.check(text)
        except ModerationUnavailableError as exc:
            # Decisión de producto: fail-closed (por defecto) rechaza la
            # petición con 503; fail-open la deja pasar sin moderar. En
            # LOG_ONLY nunca se bloquea.
            fail_closed = self._fail_closed and mode is GuardrailMode.ENFORCE
            log.error(
                "guardrail.unavailable",
                guardrail=spec.name,
                mode=mode.value,
                policy=spec.policy.value,
                error_type=str(exc),
                fail_closed=fail_closed,
                latency_ms=_elapsed_ms(start),
            )
            if fail_closed:
                raise
            return

        _log_result(spec, mode, result, _elapsed_ms(start))
        if result.triggered and mode is GuardrailMode.ENFORCE and spec.policy is FailurePolicy.EXCEPTION:
            raise InputRejectedError(spec.name)


def build_input_pipeline(settings, moderation: ModerationClient) -> InputPipeline:
    guardrails = [
        InputGuardrail(GUARDRAILS["prompt_injection"], detect_prompt_injection),
        InputGuardrail(GUARDRAILS["pii_input"], detect_pii),
        InputGuardrail(GUARDRAILS["moderation"], moderation.check, on_normalized_text=False),
    ]
    return InputPipeline(
        guardrails,
        {g.spec.name: mode_for(g.spec, settings) for g in guardrails},
        moderation_fail_closed=settings.moderation_fail_closed,
    )


# ----------------------------------------------------------------- output

# Un guardrail de output devuelve su resultado y la versión "segura" de la
# estimación (p. ej. con la PII redactada). El pipeline solo aplica esa
# versión si el guardrail está en ENFORCE con política FILTER. Aquí se
# pueden enchufar validadores adicionales (Guardrails AI Hub, LLM-as-judge).
OutputCheck = Callable[[EstimationResult], tuple[GuardrailResult, EstimationResult]]


@dataclass(frozen=True)
class OutputGuardrail:
    spec: GuardrailSpec
    check: OutputCheck


class OutputPipeline:
    def __init__(self, guardrails: Sequence[OutputGuardrail], modes: Mapping[str, GuardrailMode]) -> None:
        self._guardrails = guardrails
        self._modes = modes

    def run(self, result: EstimationResult) -> EstimationResult:
        layer_start = time.perf_counter()
        for guardrail in self._guardrails:
            spec = guardrail.spec
            mode = self._modes[spec.name]
            start = time.perf_counter()
            check_result, filtered = guardrail.check(result)
            _log_result(spec, mode, check_result, _elapsed_ms(start))
            if check_result.triggered and mode is GuardrailMode.ENFORCE and spec.policy is FailurePolicy.FILTER:
                result = filtered
        log.info("guardrail.layer_completed", stage="output", latency_ms=_elapsed_ms(layer_start))
        return result


def check_out_of_scope(result: EstimationResult) -> tuple[GuardrailResult, EstimationResult]:
    """La respuesta segura ya la produce el modelo (forzado por el validador
    del schema): aquí solo se registra, para medir cuántas hay."""
    spec = GUARDRAILS["out_of_scope"]
    return GuardrailResult(spec.name, result.is_out_of_scope, spec.policy), result


def check_output_pii(result: EstimationResult) -> tuple[GuardrailResult, EstimationResult]:
    """Busca PII en summary, nombres de fase y asunciones, y prepara la
    versión redactada."""
    spec = GUARDRAILS["pii_output"]
    categories: set[str] = set()

    def clean(text: str) -> str:
        redacted, found = redact(text)
        categories.update(found)
        return redacted

    phases = [
        phase.model_copy(
            update={"name": clean(phase.name), "assumptions": [clean(a) for a in phase.assumptions]}
        )
        for phase in result.phases
    ]
    filtered = result.model_copy(update={"summary": clean(result.summary), "phases": phases})
    check = GuardrailResult(
        spec.name,
        triggered=bool(categories),
        policy=spec.policy,
        internal_detail=",".join(sorted(categories)) or None,
    )
    return check, filtered


def build_output_pipeline(settings) -> OutputPipeline:
    guardrails = [
        OutputGuardrail(GUARDRAILS["out_of_scope"], check_out_of_scope),
        OutputGuardrail(GUARDRAILS["pii_output"], check_output_pii),
    ]
    return OutputPipeline(guardrails, {g.spec.name: mode_for(g.spec, settings) for g in guardrails})
