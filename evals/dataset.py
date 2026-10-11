"""Casos golden: entrada (uno o varios turnos) y expectativas verificables."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import ProjectType

DEFAULT_DATASET = Path(__file__).parent / "golden_dataset.json"


class CostBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int = Field(ge=0)
    max: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "CostBounds":
        if self.min > self.max:
            raise ValueError("cost_eur.min > cost_eur.max")
        return self


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    out_of_scope: bool = False
    cost_eur: CostBounds | None = None
    # Cada término admite alternativas separadas por "|" (p. ej. "pago|stripe").
    recall_terms: list[str] = Field(default_factory=list)
    min_recall: float = Field(0.6, ge=0, le=1)

    @model_validator(mode="after")
    def in_scope_needs_expectations(self) -> "Expected":
        if not self.out_of_scope and (self.cost_eur is None or not self.recall_terms):
            raise ValueError("un caso en alcance necesita cost_eur y recall_terms")
        return self


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    description: str
    project_type: ProjectType
    turns: list[str] = Field(min_length=1)
    # Ventana específica del caso (para forzar que turnos salgan de ella y
    # medir la memoria a largo plazo). None -> la de la configuración.
    window: int | None = Field(default=None, ge=1)
    expected: Expected


class GoldenDataset(BaseModel):
    cases: list[GoldenCase]

    @model_validator(mode="after")
    def unique_ids(self) -> "GoldenDataset":
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de caso duplicados")
        return self


def load_dataset(path: Path = DEFAULT_DATASET) -> GoldenDataset:
    return GoldenDataset.model_validate_json(path.read_text(encoding="utf-8"))
