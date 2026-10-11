"""Actualización de la memoria tras un turno completado.

1. Anclas heurísticas de la transcripción del turno (sin LLM).
2. Guardar el par en la ventana; si otro turno sale de ella, sus anclas se
   promueven y el summarizer lo integra en el resumen acumulado.
3. Extraer project_metadata del turno y fusionarlo (merge_metadata).

Ni el summarizer ni el extractor pueden hacer fallar el turno: ante
cualquier fallo se conserva el resumen o el metadata anterior y se registra
un warning sin contenido de la conversación.
"""

from dataclasses import dataclass

import structlog

from app.conversation.domain.anchors import extract_anchors
from app.conversation.domain.memory import ConversationMemory
from app.conversation.domain.metadata import ProjectMetadata, merge_metadata
from app.conversation.domain.usage import CallRecord, CallRole
from app.conversation.ports import ConversationSummarizer, MetadataExtractorPort

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MemoryUpdate:
    metadata: ProjectMetadata
    calls: tuple[CallRecord, ...]
    evicted_turn: int | None = None


class MemoryMaintainer:
    def __init__(self, summarizer: ConversationSummarizer, extractor: MetadataExtractorPort) -> None:
        self._summarizer = summarizer
        self._extractor = extractor

    def update(
        self,
        memory: ConversationMemory,
        metadata: ProjectMetadata,
        *,
        transcript: str,
        user_message: str,
        assistant_message: str,
    ) -> MemoryUpdate:
        """Bloqueante (ejecutar en threadpool). Nunca lanza."""
        calls: list[CallRecord] = []
        anchors = extract_anchors(transcript, memory.next_turn_number)
        evicted = memory.record_turn(user_message, assistant_message, anchors)

        if evicted is not None:
            try:
                summary = self._summarizer.summarize(memory.summary, evicted)
            except Exception as exc:  # el resumen no puede romper el turno
                log.warning("memory.summary_failed", error_type=type(exc).__name__)
            else:
                memory.update_summary(summary.parsed.summary)
                calls.append(CallRecord(CallRole.SUMMARIZER, summary.usage))
            log.info(
                "memory.turn_evicted",
                evicted_turn=evicted.number,
                anchors=len(memory.anchors),
                summary_chars=len(memory.summary),
            )

        new_metadata = self._update_metadata(metadata, user_message, assistant_message, calls)
        return MemoryUpdate(new_metadata, tuple(calls), evicted.number if evicted is not None else None)

    def _update_metadata(
        self,
        current: ProjectMetadata,
        user_message: str,
        assistant_message: str,
        calls: list[CallRecord],
    ) -> ProjectMetadata:
        try:
            extracted = self._extractor.extract_with_usage(current, user_message, assistant_message)
        except Exception as exc:  # el extractor no puede romper la estimación
            log.warning("metadata.extraction_failed", error_type=type(exc).__name__)
            return current
        calls.append(CallRecord(CallRole.METADATA_EXTRACTOR, extracted.usage))
        merged = merge_metadata(current, extracted.parsed)
        log.info(
            "metadata.updated",
            changed=merged != current,
            technologies=len(merged.mentioned_technologies),
            has_project_name=merged.project_name is not None,
            has_agreed_scope=merged.agreed_scope is not None,
        )
        return merged
