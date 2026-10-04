"""Router de sesiones conversacionales: solo traduce HTTP. La orquestación de
un turno vive en app/services/session_estimation.py y los errores se
traducen en los exception handlers de app/main.py."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.exceptions import RequestValidationError

from app.config import Settings, get_settings
from app.dependencies import get_session_estimation_service, get_session_store
from app.schemas import (
    DetailLevel,
    ErrorResponse,
    EstimationResponse,
    OutputFormat,
    ProjectType,
)
from app.services.session_estimation import SessionEstimationService, TurnInput
from app.sessions import (
    Session,
    SessionCreated,
    SessionNotFoundError,
    SessionState,
    SessionStore,
    short_session_id,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Sesión inexistente o expirada (session_not_found)."}}


async def get_session(session_id: UUID, store: SessionStore = Depends(get_session_store)) -> Session:
    """El path param tipado como UUID: un formato inválido es 422 (FastAPI).
    async: así el bind de contextvars ocurre en el contexto de la petición
    (una dependencia síncrona corre en un threadpool y el bind se perdería)."""
    session = store.get(session_id)
    if session is None:
        raise SessionNotFoundError()
    structlog.contextvars.bind_contextvars(session=short_session_id(session.session_id))
    return session


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionCreated,
    summary="Crear una sesión conversacional vacía",
    responses={503: {"model": ErrorResponse, "description": "Máximo de sesiones alcanzado (sessions_exhausted)."}},
)
async def create_session(store: SessionStore = Depends(get_session_store)) -> SessionCreated:
    session = store.create()
    log.info("session.created", session=short_session_id(session.session_id), sessions=len(store))
    return SessionCreated(session_id=session.session_id)


@router.get(
    "/{session_id}",
    response_model=SessionState,
    summary="Estado de la sesión: project_metadata y turnos en el historial",
    responses=NOT_FOUND,
)
async def read_session(session: Session = Depends(get_session)) -> SessionState:
    return SessionState(
        session_id=session.session_id,
        project_metadata=session.metadata,
        turns=len(session.history),
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar la sesión (Nueva conversación)",
    responses=NOT_FOUND,
)
async def delete_session(
    session: Session = Depends(get_session),
    store: SessionStore = Depends(get_session_store),
) -> None:
    store.delete(session.session_id)
    log.info("session.deleted", sessions=len(store))


def _transcript_error(error_type: str, msg: str, ctx: dict | None = None) -> RequestValidationError:
    error = {"type": error_type, "loc": ("body", "transcript"), "msg": msg}
    if ctx:
        error["ctx"] = ctx
    return RequestValidationError([error])


@router.post(
    "/{session_id}/estimate",
    response_model=EstimationResponse,
    summary="Estimar un turno de la conversación (transcripción + adjuntos opcionales)",
    responses={
        400: {"model": ErrorResponse, "description": "Entrada rechazada por un guardrail (input_rejected)."},
        **NOT_FOUND,
        413: {"model": ErrorResponse, "description": "Adjuntos demasiado grandes o demasiados (attachment_too_large)."},
        415: {"model": ErrorResponse, "description": "Adjunto que no es PDF ni DOCX real (unsupported_attachment)."},
        422: {"description": "Formulario inválido, o adjunto cifrado o dañado (unreadable_attachment)."},
        502: {"model": ErrorResponse, "description": "El modelo no devolvió una estimación válida (estimation_failed)."},
        503: {"model": ErrorResponse, "description": "Moderación o proveedores LLM no disponibles."},
    },
)
async def estimate_turn(
    session: Session = Depends(get_session),
    transcript: str = Form(..., description="Transcripción de la reunión (texto)."),
    project_type: ProjectType = Form(..., description="Tipo de proyecto."),
    detail_level: DetailLevel = Form(DetailLevel.MEDIUM),
    output_format: OutputFormat = Form(OutputFormat.PHASES_TABLE),
    attachments: list[UploadFile] | None = File(None, description="Adjuntos PDF o DOCX (opcional)."),
    settings: Settings = Depends(get_settings),
    service: SessionEstimationService = Depends(get_session_estimation_service),
) -> EstimationResponse:
    transcript = transcript.strip()
    if not transcript:
        raise _transcript_error("string_too_short", "La transcripción no puede estar vacía.", {"min_length": 1})
    if len(transcript) > settings.max_transcript_chars:
        raise _transcript_error(
            "string_too_long",
            f"String should have at most {settings.max_transcript_chars} characters",
            {"max_length": settings.max_transcript_chars},
        )
    turn = TurnInput(transcript, project_type, detail_level, output_format)
    return await service.estimate_turn(session, turn, attachments or [])
