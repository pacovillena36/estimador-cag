"""Orquestación de un turno de una sesión conversacional (sin FastAPI).

Dentro de `async with session.lock` (dos peticiones a la misma sesión no
pueden intercalar historial ni metadata):
1. Extraer el texto de los adjuntos (en threadpool: es bloqueante).
2. Input guardrails sobre la transcripción y el texto de los adjuntos
   (moderación, injection, PII), reutilizando el pipeline de /estimate.
3. Componer el mensaje de usuario (<transcript> + <attachment_content>).
4. Renderizar el system prompt con el project_metadata actual.
5. messages = historial.to_messages_list(system) + [mensaje actual].
6. LLM (wrapper existente: timeout, reintentos, fallback) con salida
   validada contra EstimationResult; si no valida -> 502 y el historial no
   cambia. Después, output guardrails.
7. history.add_turn(mensaje de usuario, estimación en JSON).
8. Actualizar project_metadata (extractor; nunca hace fallar el turno).

El historial solo avanza en turnos completados con éxito: cualquier error
de los pasos 1-6 sale como excepción antes del paso 7.

No se usa el caché semántico: con historial, dos transcripciones parecidas
no son peticiones equivalentes.

Presupuesto de tokens: fuera de alcance en esta fase. Los límites de
caracteres de transcripción y adjuntos acotan cada par, y la ventana
(MAX_TURNS) el número de pares, pero no se cuentan tokens.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial

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
from app.guardrails.pipeline import InputPipeline, OutputPipeline, description_hash
from app.metadata_extractor import MetadataExtractor
from app.prompts.loader import render_session_system_prompt
from app.schemas import (
    MIN_CONFIDENCE_CONTEXT_KEY,
    DetailLevel,
    EstimationResponse,
    EstimationResult,
    OutputFormat,
    ProjectType,
)
from app.services.llm_gateway import LLMGateway
from app.sessions import Session

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TurnInput:
    transcript: str
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat


def _guardrail_text(transcript: str, attachments: Sequence[ExtractedAttachment]) -> str:
    return "\n\n".join([transcript, *(a.text for a in attachments)])


class SessionEstimationService:
    def __init__(
        self,
        gateway: LLMGateway,
        input_pipeline: InputPipeline,
        output_pipeline: OutputPipeline,
        extractor: MetadataExtractor,
        *,
        prompt_version: str,
        min_confidence_pct: int,
        attachment_limits: AttachmentLimits,
    ) -> None:
        self._gateway = gateway
        self._input = input_pipeline
        self._output = output_pipeline
        self._extractor = extractor
        self._prompt_version = prompt_version
        self._min_confidence_pct = min_confidence_pct
        self._limits = attachment_limits

    async def estimate_turn(
        self, session: Session, turn: TurnInput, uploads: Sequence[UploadFile]
    ) -> EstimationResponse:
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
                previous_turns=len(session.history),
            )
            attachments = await run_in_threadpool(extract_attachments, raws, self._limits)
            await run_in_threadpool(self._input.run, _guardrail_text(turn.transcript, attachments))

            user_message = compose_user_message(turn.transcript, attachments)
            system = render_session_system_prompt(
                project_type=turn.project_type,
                detail_level=turn.detail_level,
                output_format=turn.output_format,
                project_metadata=session.metadata,
                version=self._prompt_version,
                min_confidence_pct=self._min_confidence_pct,
            )
            messages = session.history.to_messages_list(system) + [
                {"role": "user", "content": user_message}
            ]
            result = await run_in_threadpool(
                partial(
                    self._gateway.complete_structured_messages,
                    messages,
                    EstimationResult,
                    context={MIN_CONFIDENCE_CONTEXT_KEY: self._min_confidence_pct},
                )
            )
            result = self._output.run(result)

            assistant_message = result.model_dump_json()
            session.history.add_turn(user_message, assistant_message)
            session.metadata = await run_in_threadpool(
                self._extractor.update, session.metadata, user_message, assistant_message
            )

            response = EstimationResponse(result=result, prompt_version=self._prompt_version)
            log.info(
                "session.turn_completed",
                turns=len(session.history),
                out_of_scope=response.out_of_scope,
                total_hours=result.total_hours,
                truncated_attachments=sum(a.truncated for a in attachments),
            )
            return response
