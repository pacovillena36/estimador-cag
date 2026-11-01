"""POST /search: los k chunks más cercanos a la query por distancia coseno.

El 502 de proveedor de embeddings lo traduce app/main.py.
"""

from fastapi import APIRouter, Depends

from app.dependencies import get_search_service
from app.schemas import ErrorResponse
from app.search_schemas import SearchRequest, SearchResponse
from app.services.search import SearchService

router = APIRouter(tags=["search"])


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={502: {"model": ErrorResponse, "description": "Fallo de la API de embeddings"}},
    summary="Búsqueda semántica sobre los chunks de presupuestos (top-k por distancia coseno)",
)
async def search(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    outcome = await service.search(request.query, request.k)
    return SearchResponse(
        query=request.query,
        k=request.k,
        search_time_ms=outcome.search_time_ms,
        results=outcome.results,
    )
