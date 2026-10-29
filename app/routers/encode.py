"""POST /embeddings/encode: el embedding de uno o varios textos, sin persistir.

Solo valida, llama al servicio y traduce el resultado a HTTP. El 502 de
proveedor lo traduce app/main.py.
"""

from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError

from app.config import Settings, get_settings
from app.dependencies import get_encoding_service, limit_ingest_body_size
from app.embedding_pipeline.embedder import MAX_INPUT_TOKENS
from app.embedding_pipeline.schemas import EncodeRequest, EncodeResponse
from app.schemas import ErrorResponse
from app.services.encoding import EncodingService, TextTooLongError

router = APIRouter(tags=["embeddings"])


@router.post(
    "/encode",
    response_model=EncodeResponse,
    dependencies=[Depends(limit_ingest_body_size)],
    responses={
        413: {"model": ErrorResponse, "description": "Body demasiado grande"},
        502: {"model": ErrorResponse, "description": "Fallo de la API de embeddings"},
    },
    summary="Calcula el embedding de uno o varios textos (mismo modelo que la ingesta y /search)",
)
async def encode(
    request: EncodeRequest,
    service: EncodingService = Depends(get_encoding_service),
    settings: Settings = Depends(get_settings),
) -> EncodeResponse:
    try:
        outcome = await service.encode(request.texts)
    except TextTooLongError as exc:
        raise RequestValidationError(
            [
                {"type": "text_too_long", "loc": ("body", "texts", i), "msg": f"Text exceeds {MAX_INPUT_TOKENS} tokens"}
                for i in exc.indices
            ]
        ) from None
    return EncodeResponse(
        model=settings.embedding_model,
        embedding_dimension=settings.embedding_dim,
        embeddings=outcome.embeddings,
        total_tokens=outcome.total_tokens,
        encode_time_ms=outcome.encode_time_ms,
    )
