"""Contrato entre el cliente y el servicio IA (Pydantic v2).

El cliente Streamlit reutiliza estas mismas clases para validar el
formulario antes de enviarlo, así que cliente y servicio no pueden
desincronizarse.

`EstimationResult` es además el contrato con el LLM: su JSON Schema
(`model_json_schema()`) se envía al proveedor vía Instructor, y la
respuesta del modelo se valida contra él. Es la única definición del
shape de la estimación: no se repite a mano en los prompts.

Este módulo no importa la configuración del servidor (lo importa también el
cliente). Los umbrales de producto que dependen de settings llegan a los
validadores por el contexto de validación (ver `check_consistency`).
"""

from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    computed_field,
    model_validator,
)

# Prefijo del summary de un resultado fuera de alcance. Única definición:
# lo usan el system prompt (vía el loader) y los validadores.
OUT_OF_SCOPE_PREFIX = "Out of scope:"

# Confianza mínima por defecto para una estimación en alcance (umbral de
# producto, configurable con MIN_CONFIDENCE_PCT). El servidor lo pasa a la
# validación con esta clave de contexto.
DEFAULT_MIN_CONFIDENCE_PCT = 30
MIN_CONFIDENCE_CONTEXT_KEY = "min_confidence_pct"

# Techo absoluto de la descripción. El servidor aplica además el límite
# configurable DESCRIPTION_MAX_LENGTH (menor o igual que este).
DESCRIPTION_MIN_LENGTH = 20
DESCRIPTION_HARD_MAX_LENGTH = 10_000

MAX_PHASES = 20


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
    # extra="forbid": un campo desconocido es un error, no se ignora.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    description: str = Field(
        min_length=DESCRIPTION_MIN_LENGTH,
        max_length=DESCRIPTION_HARD_MAX_LENGTH,
        description=(
            "Descripción del proyecto a estimar. El servidor aplica además "
            "su límite configurado (DESCRIPTION_MAX_LENGTH)."
        ),
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
    summary: str = Field(
        description=(
            "Resumen de la estimación para el cliente. Si el proyecto está "
            f"fuera de alcance, empieza por '{OUT_OF_SCOPE_PREFIX}'."
        )
    )
    total_hours: int = Field(ge=0, description="Suma de las horas de las fases.")
    total_duration_weeks: int = Field(
        ge=0, description="Duración total en semanas (fases consecutivas)."
    )
    total_cost_eur: int = Field(ge=0, description="Suma del coste de las fases, en euros.")
    confidence_pct: int = Field(
        ge=0, le=100, description="Confianza global en la estimación (0-100)."
    )
    phases: list[Phase] = Field(
        max_length=MAX_PHASES, description="Fases del proyecto, en orden."
    )

    @property
    def is_out_of_scope(self) -> bool:
        return self.summary.startswith(OUT_OF_SCOPE_PREFIX)

    @model_validator(mode="after")
    def check_consistency(self, info: ValidationInfo) -> "EstimationResult":
        """Política FIX_RETRY: si falla, Instructor reenvía el error al
        modelo para que corrija su respuesta.

        El umbral de confianza solo se comprueba cuando llega en el contexto
        de validación (lo pasa el servidor desde MIN_CONFIDENCE_PCT). Así el
        cliente, que no conoce la configuración del servidor, no rechaza al
        revalidar una respuesta que el servidor ya aceptó.
        """
        if self.is_out_of_scope:
            if (
                self.phases
                or self.total_hours
                or self.total_duration_weeks
                or self.total_cost_eur
                or self.confidence_pct
            ):
                raise ValueError(
                    "Out-of-scope results must have phases=[] and total_hours, "
                    "total_duration_weeks, total_cost_eur and confidence_pct set to 0"
                )
            return self

        min_confidence = (info.context or {}).get(MIN_CONFIDENCE_CONTEXT_KEY)
        if min_confidence is not None and self.confidence_pct < min_confidence:
            raise ValueError(
                f"Confidence below {min_confidence}% requires an out-of-scope "
                f"result (summary starting with '{OUT_OF_SCOPE_PREFIX}', phases=[] "
                "and all totals and confidence_pct set to 0)"
            )
        if not self.phases:
            raise ValueError("In-scope results require at least one phase")
        if self.total_duration_weeks < 1:
            raise ValueError("In-scope results require total_duration_weeks >= 1")

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
    prompt_version: str = Field(description="Versión del prompt usada (p. ej. v3).")

    @computed_field(  # type: ignore[prop-decorator]
        description=(
            "true si el modelo no pudo estimar (fuera de alcance o confianza "
            "insuficiente): result tiene phases=[] y todo a 0. Calculado en el "
            "servidor; los consumidores no deben interpretar el summary."
        )
    )
    @property
    def out_of_scope(self) -> bool:
        return self.result.is_out_of_scope


class ErrorDetail(BaseModel):
    code: str = Field(description="Código estable del error (p. ej. input_rejected).")
    message: str = Field(description="Mensaje genérico, apto para mostrar al usuario.")
    request_id: str | None = Field(description="Identificador de la petición (X-Request-ID).")


class ErrorResponse(BaseModel):
    """Cuerpo único de error. Nunca incluye detalles internos (proveedor,
    guardrail que disparó, trazas): eso va solo a los logs."""

    error: ErrorDetail
