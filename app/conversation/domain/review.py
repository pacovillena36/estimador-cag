"""Actor-Critic-Boss: modelos de la revisión de una estimación.

- Actor: la estimación normal (EstimationResult).
- Critic: revisa la estimación y devuelve un CriticReport.
- Boss: solo si el critic pide revisión; produce la estimación final.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_CRITIC_ISSUES = 10
MAX_ISSUE_CHARS = 400


class EstimationMode(StrEnum):
    ACTOR = "actor"  # una sola llamada (por defecto)
    ACB = "acb"  # Actor-Critic-Boss


class CriticVerdict(StrEnum):
    OK = "ok"
    REVISE = "revise"


class CriticIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "missing_phase",
        "inconsistent_hours",
        "ignored_constraint",
        "unjustified_assumption",
        "other",
    ] = Field(description="Tipo de problema.")
    description: str = Field(
        min_length=1, max_length=MAX_ISSUE_CHARS, description="Qué falla y por qué, en una o dos frases."
    )


class CriticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: CriticVerdict = Field(
        description="ok si la estimación es aceptable tal cual; revise si debe corregirse."
    )
    issues: list[CriticIssue] = Field(
        default_factory=list,
        max_length=MAX_CRITIC_ISSUES,
        description="Problemas detectados (obligatorios si verdict es revise).",
    )

    @model_validator(mode="after")
    def revise_needs_issues(self) -> "CriticReport":
        if self.verdict is CriticVerdict.REVISE and not self.issues:
            raise ValueError("verdict=revise requiere al menos un issue")
        return self
