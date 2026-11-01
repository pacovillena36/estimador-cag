"""Contrato de POST /search (búsqueda semántica sobre los chunks)."""

from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from app.config import get_settings

_settings = get_settings()


class SearchRequest(BaseModel):
    # Se recorta antes de validar: una query de solo espacios es vacía.
    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=_settings.search_query_max_length),
    ]
    k: int = Field(5, ge=1, le=_settings.search_max_k)


class SearchResult(BaseModel):
    chunk_id: int
    document_id: int
    chunk_type: str
    content: str
    distance: float = Field(description="Distancia coseno (0 = idéntico, 2 = opuesto)")
    metadata: dict[str, Any]


class SearchResponse(BaseModel):
    query: str
    k: int
    search_time_ms: int
    results: list[SearchResult]
