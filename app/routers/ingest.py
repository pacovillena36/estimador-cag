"""POST /embeddings/ingest: persiste un presupuesto (documento + chunks con
su embedding) en Postgres.

Solo valida, llama al servicio y traduce el resultado a HTTP. El 409 (cuerpo
a nivel raíz) y el 502 de proveedor los traduce app/main.py.
"""

from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError

from app.config import Settings, get_settings
from app.dependencies import get_ingestion_service, limit_ingest_body_size
from app.embedding_pipeline.schemas import DocumentConflictResponse, IngestRequest, IngestResponse
from app.schemas import ErrorResponse
from app.services.ingestion import IngestionService, InvalidDocumentError

router = APIRouter(tags=["embeddings"])


@router.post(
    "/ingest",
    response_model=IngestResponse,
    dependencies=[Depends(limit_ingest_body_size)],
    responses={
        409: {"model": DocumentConflictResponse, "description": "source_path ya ingestado"},
        413: {"model": ErrorResponse, "description": "Body demasiado grande"},
        502: {"model": ErrorResponse, "description": "Fallo de la API de embeddings (no se persiste nada)"},
    },
    summary="Trocea un presupuesto, calcula sus embeddings y lo persiste en pgvector",
)
async def ingest(
    request: IngestRequest,
    service: IngestionService = Depends(get_ingestion_service),
    settings: Settings = Depends(get_settings),
) -> IngestResponse:
    try:
        result = await service.ingest(request)
    except InvalidDocumentError as exc:
        raise RequestValidationError(
            [{"type": exc.code, "loc": ("body", "content"), "msg": exc.message}]
        ) from None
    return IngestResponse(
        document_id=result.document_id,
        chunks_created=result.chunks_created,
        embedding_dimension=settings.embedding_dim,
        ingestion_time_ms=result.ingestion_time_ms,
    )
