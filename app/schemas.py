"""Contrato entre el cliente y el servicio IA (Pydantic v2).

El cliente Streamlit reutiliza estas mismas clases para validar el
formulario antes de enviarlo, así que cliente y servicio no pueden
desincronizarse.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ProjectType(str, Enum):
    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(str, Enum):
    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(str, Enum):
    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class EstimationRequest(BaseModel):
    # Se recortan espacios/saltos de línea al principio y al final antes de
    # validar la longitud: no cambian el significado y, al pegar texto,
    # suelen variar entre envíos, lo que haría fallar la caché exact-match.
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(
        min_length=20,
        max_length=2000,
        description="Descripción del proyecto a estimar.",
    )
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat


class EstimationResponse(BaseModel):
    text: str = Field(description="Estimación generada por el modelo.")
    prompt_version: str = Field(description="Versión del prompt usada (p. ej. v1).")
