"""Orquestación de un turno de una sesión conversacional (sin FastAPI).

Dentro de `async with session.lock` (dos peticiones a la misma sesión no
pueden intercalar historial ni metadata):
1. Extraer el texto de los adjuntos (en threadpool: es bloqueante).
2. Input guardrails sobre la transcripción y el texto de los adjuntos
   (moderación, injection, PII), reutilizando el pipeline de /estimate.
3. Componer el mensaje de usuario (<transcript> + <attachment_content>).
4. Resolver el tier del turno (TierPolicy, reglas deterministas).
5. Renderizar el system prompt con project_metadata, resumen y anclas.
6. messages = system + ventana deslizante + mensaje actual.
7. EstimationEngine: actor y, en modo acb, critic + boss. Si el actor no
   devuelve una estimación válida -> 502 y la memoria no cambia. Después,
   output guardrails.
8. MemoryMaintainer: guardar el turno, anclas, resumen (si un turno sale de
   la ventana) y project_metadata. Nunca hace fallar el turno.
9. Telemetría de la sesión: tier, regla y uso de todas las llamadas.
10. Un único evento `turn_observed` (TurnObservation) con los contadores
    agregados del turno; se guarda también en la sesión.

La memoria solo avanza en turnos completados con éxito: cualquier error de
los pasos 1-7 sale como excepción antes del paso 8.

No se usa el caché semántico: con historial, dos transcripciones parecidas
no son peticiones equivalentes.

Presupuesto de tokens: no se cuentan tokens antes de llamar. Lo acotan los
límites de caracteres (transcripción, adjuntos, resumen y anclas) y la
ventana (MAX_CONVERSATION_TURNS); el uso real se mide después en cada
llamada (SessionTelemetry).
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from functools import partial

import time

import structlog
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.attachments import (
    AttachmentLimits,
    ExtractedAttachment,
    compose_user_message,
    extract_attachments,
    read_attachments,
)
from app.conversation.application.estimation_engine import EngineOutcome, EstimationEngine
from app.conversation.application.memory_maintainer import MemoryMaintainer
from app.conversation.domain.review import EstimationMode
from app.conversation.domain.tiering import TierDecision, TierPolicy, TierSignals
from app.conversation.domain.usage import CallRole, TurnUsage
from app.conversation.ports import ConversationSnapshot
from app.guardrails.pipeline import InputPipeline, OutputPipeline, description_hash
from app.observability.turn_observation import TURN_OBSERVED_EVENT, CacheHitKind, TurnObservation
from app.prompts.loader import render_session_system_prompt
from app.schemas import (
    DetailLevel,
    EstimationResponse,
    OutputFormat,
    ProjectType,
)
from app.sessions import Session, short_session_id

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TurnInput:
    transcript: str
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
    mode: EstimationMode | None = None  # None -> el modo por defecto del servicio


@dataclass(frozen=True)
class TurnOutcome:
    """Respuesta HTTP del turno más lo que se observó al producirla."""

    response: EstimationResponse
    tier: TierDecision
    mode: EstimationMode
    usage: TurnUsage
    engine: EngineOutcome
    observation: TurnObservation


def _cache_hit_kind(usage: TurnUsage) -> CacheHitKind:
    """El turno cuenta como cache hit si la llamada del actor salió de la
    caché exact-match del gateway (las demás llamadas son auxiliares)."""
    actor = usage.by_role(CallRole.ACTOR)
    return "exact" if actor and all(c.usage.cached for c in actor) else "none"


def _guardrail_text(transcript: str, attachments: Sequence[ExtractedAttachment]) -> str:
    return "\n\n".join([transcript, *(a.text for a in attachments)])


class SessionEstimationService:
    def __init__(
        self,
        input_pipeline: InputPipeline,
        output_pipeline: OutputPipeline,
        engine: EstimationEngine,
        memory: MemoryMaintainer,
        tier_policy: TierPolicy,
        *,
        prompt_version: str,
        min_confidence_pct: int,
        attachment_limits: AttachmentLimits,
        default_mode: EstimationMode = EstimationMode.ACTOR,
    ) -> None:
        self._input = input_pipeline
        self._output = output_pipeline
        self._engine = engine
        self._memory = memory
        self._tier_policy = tier_policy
        self._prompt_version = prompt_version
        self._min_confidence_pct = min_confidence_pct
        self._limits = attachment_limits
        self._default_mode = default_mode

    async def estimate_turn(
        self, session: Session, turn: TurnInput, uploads: Sequence[UploadFile]
    ) -> TurnOutcome:
        started = time.perf_counter()
        mode = turn.mode or self._default_mode
        structlog.contextvars.bind_contextvars(
            prompt_version=self._prompt_version,
            description_sha256=description_hash(turn.transcript),
        )
        # Lectura y validación de tamaño/tipo antes de tomar el lock: un
        # adjunto inválido no bloquea la sesión.
        raws = await read_attachments(uploads, self._limits)

        async with session.lock:
            log.info(
                "session.turn_requested",
                transcript_chars=len(turn.transcript),
                attachments=len(raws),
                attachment_bytes=sum(len(r.data) for r in raws),
                previous_turns=len(session.memory),
                mode=mode.value,
            )
            attachments = await run_in_threadpool(extract_attachments, raws, self._limits)
            await run_in_threadpool(self._input.run, _guardrail_text(turn.transcript, attachments))

            user_message = compose_user_message(turn.transcript, attachments)
            decision = self._tier_policy.resolve(
                TierSignals(
                    transcript=turn.transcript,
                    attachment_chars=sum(len(a.text) for a in attachments),
                    previous_confidence_pct=session.last_confidence_pct,
                    has_summary=bool(session.memory.summary),
                )
            )
            messages = session.memory.to_messages(self._system_prompt(session, turn)) + [
                {"role": "user", "content": user_message}
            ]
            snapshot = ConversationSnapshot(
                user_message=user_message,
                project_metadata=session.metadata,
                summary=session.memory.summary,
                anchors=session.memory.anchors,
            )
            outcome = await run_in_threadpool(
                partial(self._engine.estimate, messages, tier=decision.tier, mode=mode, snapshot=snapshot)
            )
            result = self._output.run(outcome.result)

            assistant_message = result.model_dump_json()
            update = await run_in_threadpool(
                partial(
                    self._memory.update,
                    session.memory,
                    session.metadata,
                    transcript=turn.transcript,
                    user_message=user_message,
                    assistant_message=assistant_message,
                )
            )
            session.metadata = update.metadata
            session.last_confidence_pct = None if result.is_out_of_scope else result.confidence_pct
            usage = TurnUsage(outcome.calls + update.calls)
            session.telemetry.record(decision, usage)

            response = EstimationResponse(result=result, prompt_version=self._prompt_version)
            log.info(
                "session.turn_completed",
                turns=len(session.memory),
                out_of_scope=response.out_of_scope,
                total_hours=result.total_hours,
                truncated_attachments=sum(a.truncated for a in attachments),
                tier=decision.tier.value,
                tier_rule=decision.rule,
                mode=mode.value,
                acb_revised=outcome.review.revised if outcome.review else None,
                llm_calls=len(usage.calls),
                cost_usd=usage.cost_usd,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                anchors=len(session.memory.anchors),
                summary_chars=len(session.memory.summary),
            )
            observation = TurnObservation(
                turn_index=session.memory.history.recorded,
                session_id=short_session_id(session.session_id),
                enriched_transcript_chars=len(user_message),
                attachments_total_chars=sum(len(a.text) for a in attachments),
                messages_in_window=session.memory.message_count,
                anchors_count=len(session.memory.anchors),
                summary_chars=len(session.memory.summary),
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cost_usd=usage.cost_usd,
                latency_ms=round((time.perf_counter() - started) * 1000),
                cache_hit_kind=_cache_hit_kind(usage),
                last_resolved_tier=decision.tier.value,
            )
            session.last_turn_observation = observation
            log.info(TURN_OBSERVED_EVENT, **asdict(observation))
            return TurnOutcome(response, decision, mode, usage, outcome, observation)

    def _system_prompt(self, session: Session, turn: TurnInput) -> str:
        return render_session_system_prompt(
            project_type=turn.project_type,
            detail_level=turn.detail_level,
            output_format=turn.output_format,
            project_metadata=session.metadata,
            version=self._prompt_version,
            min_confidence_pct=self._min_confidence_pct,
            summary=session.memory.summary,
            anchors=session.memory.anchors,
        )
