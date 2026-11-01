"""ProjectMetadata: hechos estructurados del proyecto en curso."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_TECHNOLOGIES = 30
MAX_TECHNOLOGY_CHARS = 60
MAX_AGREED_SCOPE_CHARS = 2000
MAX_PROJECT_NAME_CHARS = 200

Technology = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TECHNOLOGY_CHARS)]


class ProjectMetadata(BaseModel):
    """Hechos acumulados del proyecto en curso. La genera el extractor (LLM),
    así que todo se valida aquí: extra="forbid" impide colar campos."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_name: str | None = Field(
        default=None, max_length=MAX_PROJECT_NAME_CHARS, description="Nombre del proyecto."
    )
    assumed_team_size: int | None = Field(
        default=None, ge=1, le=500, description="Tamaño del equipo asumido (personas)."
    )
    mentioned_technologies: list[Technology] = Field(
        default_factory=list,
        max_length=MAX_TECHNOLOGIES,
        description="Tecnologías mencionadas en la conversación.",
    )
    agreed_scope: str | None = Field(
        default=None,
        max_length=MAX_AGREED_SCOPE_CHARS,
        description="Alcance acordado hasta ahora, en pocas frases.",
    )

    def is_empty(self) -> bool:
        return (
            self.project_name is None
            and self.assumed_team_size is None
            and not self.mentioned_technologies
            and not self.agreed_scope
        )


def merge_metadata(current: ProjectMetadata, new: ProjectMetadata) -> ProjectMetadata:
    """Reglas de merge (no se sobrescribe a ciegas):
    - Escalares (project_name, assumed_team_size): solo si el nuevo no es None.
    - mentioned_technologies: unión sin duplicados (sin distinguir
      mayúsculas, conservando la primera forma vista) y con el límite de
      longitud de la lista.
    - agreed_scope: se reemplaza si el nuevo no está vacío.
    """
    technologies: list[str] = []
    seen: set[str] = set()
    for technology in [*current.mentioned_technologies, *new.mentioned_technologies]:
        key = technology.casefold()
        if key not in seen and len(technologies) < MAX_TECHNOLOGIES:
            seen.add(key)
            technologies.append(technology)

    return ProjectMetadata(
        project_name=new.project_name if new.project_name is not None else current.project_name,
        assumed_team_size=(
            new.assumed_team_size if new.assumed_team_size is not None else current.assumed_team_size
        ),
        mentioned_technologies=technologies,
        agreed_scope=new.agreed_scope if new.agreed_scope else current.agreed_scope,
    )
