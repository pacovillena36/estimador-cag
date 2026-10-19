"""Modelos del pipeline de embeddings (Pydantic v2).

Budget y BudgetComponent reflejan el JSON normalizado de la Sesión 06;
Chunk y EmbeddedChunk son lo que produce el pipeline. Los identificadores
se restringen a un alfabeto seguro porque forman el chunk_id
("{budget_id}::{component_id}") y viajan a los logs.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Universo cerrado de valores del dataset normalizado: un valor nuevo debe
# entrar aquí a propósito, no colarse como texto libre en los filtros.
Sector = Literal["finance", "ecommerce", "healthcare", "industrial"]
Complexity = Literal["low", "medium", "high"]

_ID_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"

# Techo de presupuestos por petición: protege del abuso de coste (cada
# componente es una entrada facturada en la API de embeddings).
MAX_BUDGETS_PER_REQUEST = 200


class ClientMetadata(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sector: Sector
    country: str = Field(pattern=r"^[A-Z]{2}$", description="ISO 3166-1 alfa-2")


class BudgetComponent(BaseModel):
    component_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    tech_stack: list[str] = Field(default_factory=list)
    estimated_hours: float = Field(gt=0)
    complexity: Complexity
    dependencies: list[str] = Field(default_factory=list)


class Budget(BaseModel):
    budget_id: str = Field(pattern=_ID_PATTERN)
    client_metadata: ClientMetadata
    project_summary: str = Field(min_length=1)
    main_technology: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1990, le=2100)
    total_estimated_hours: float = Field(ge=0)
    components: list[BudgetComponent] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_component_ids(self) -> "Budget":
        # Un id repetido daría dos chunks con el mismo chunk_id.
        ids = [component.component_id for component in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("component_id must be unique within a budget")
        return self


class Chunk(BaseModel):
    chunk_id: str
    text: str
    # Campos filtrables: viajan con el chunk pero no se embeben.
    metadata: dict[str, str | int | float]
    token_count: int = Field(ge=0)


class EmbeddedChunk(Chunk):
    embedding: list[float]


class IngestRequest(BaseModel):
    budgets: list[Budget] = Field(min_length=1, max_length=MAX_BUDGETS_PER_REQUEST)

    @model_validator(mode="after")
    def unique_budget_ids(self) -> "IngestRequest":
        ids = [budget.budget_id for budget in self.budgets]
        if len(ids) != len(set(ids)):
            raise ValueError("budget_id must be unique within a request")
        return self


class IngestStats(BaseModel):
    total_budgets: int
    total_chunks: int
    total_tokens: int
    estimated_cost_usd: float


class IngestResponse(BaseModel):
    chunks: list[EmbeddedChunk]
    stats: IngestStats
