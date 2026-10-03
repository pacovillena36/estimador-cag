"""Contrato entre el cliente y el servicio IA (Pydantic v2).

El cliente Streamlit reutiliza estas mismas clases para validar el
formulario antes de enviarlo, así que cliente y servicio no pueden
desincronizarse.

`EstimationResult` es además el contrato con el LLM: su JSON Schema
(`model_json_schema()`) se envía al proveedor vía Instructor, y la
respuesta del modelo se valida contra él. Es la única definición del
shape de la estimación: no se repite a mano en los prompts.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


# Los mensajes de los validadores van en inglés a propósito: Instructor los
# reenvía al modelo en los reintentos y deben ser claros para el LLM.


class Phase(BaseModel):
    name: str = Field(description="Nombre corto de la fase.")
    hours: int = Field(ge=1, description="Horas de trabajo del equipo en la fase.")
    duration_weeks: int = Field(ge=1, le=52, description="Duración de la fase en semanas.")
    cost_eur: int = Field(ge=0, description="Coste de la fase en euros.")
    confidence_pct: int = Field(
        ge=0, le=100, description="Confianza en la estimación de la fase (0-100)."
    )
    assumptions: list[str] = Field(
        description="Asunciones en las que se basa la estimación de la fase."
    )


class EstimationResult(BaseModel):
    summary: str = Field(description="Resumen de la estimación para el cliente.")
    total_hours: int = Field(ge=1, description="Suma de las horas de las fases.")
    total_duration_weeks: int = Field(
        ge=1, description="Duración total en semanas (fases consecutivas)."
    )
    total_cost_eur: int = Field(ge=0, description="Suma del coste de las fases, en euros.")
    confidence_pct: int = Field(
        ge=0, le=100, description="Confianza global en la estimación (0-100)."
    )
    phases: list[Phase] = Field(min_length=1, description="Fases del proyecto, en orden.")

    @model_validator(mode="after")
    def totals_must_match_phases(self) -> "EstimationResult":
        sum_hours = sum(p.hours for p in self.phases)
        sum_weeks = sum(p.duration_weeks for p in self.phases)
        sum_cost = sum(p.cost_eur for p in self.phases)

        if sum_hours != self.total_hours:
            raise ValueError(
                f"total_hours ({self.total_hours}) must equal the sum of phase hours ({sum_hours})"
            )

        # Tolerancia de ±1 semana en duración
        if abs(sum_weeks - self.total_duration_weeks) > 1:
            raise ValueError(
                f"total_duration_weeks ({self.total_duration_weeks}) does not match "
                f"the sum of phase duration_weeks ({sum_weeks})"
            )

        # Tolerancia del 5% en coste (protegido contra división por cero)
        if self.total_cost_eur == 0:
            cost_matches = sum_cost == 0
        else:
            cost_matches = abs(sum_cost - self.total_cost_eur) / self.total_cost_eur <= 0.05
        if not cost_matches:
            raise ValueError(
                f"total_cost_eur ({self.total_cost_eur}) does not match "
                f"the sum of phase cost_eur ({sum_cost})"
            )

        return self


class EstimationResponse(BaseModel):
    result: EstimationResult = Field(description="Estimación estructurada y validada.")
    prompt_version: str = Field(description="Versión del prompt usada (p. ej. v1).")
