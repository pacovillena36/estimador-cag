"""Extractor de project_metadata: segunda llamada al LLM por turno.

- Salida estructurada (Instructor, response_model=ProjectMetadata): el
  proveedor recibe el schema y la respuesta se valida con
  ProjectMetadata.model_validate (extra="forbid", límites de longitud).
  Instructor ya parsea los argumentos de la herramienta, así que no hay
  fences de markdown que limpiar. Nada sin validar entra en la sesión.
- Temperatura 0, max_tokens acotado y el timeout del wrapper.
- Merge con reglas (no se sobrescribe a ciegas); ver merge_metadata.
- Si falla (timeout, proveedor caído, salida inválida), la estimación NO
  falla: se registra un warning sin contenido de la conversación y se
  conserva el metadata anterior.
"""

import structlog

from app.prompts.loader import render_metadata_extractor_prompt
from app.services.llm_gateway import LLMGateway
from app.sessions import MAX_TECHNOLOGIES, ProjectMetadata

log = structlog.get_logger(__name__)


def merge_metadata(current: ProjectMetadata, new: ProjectMetadata) -> ProjectMetadata:
    """Reglas de merge:
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


class MetadataExtractor:
    def __init__(self, gateway: LLMGateway, *, max_tokens: int, prompt_version: str = "v1") -> None:
        self._gateway = gateway
        self._max_tokens = max_tokens
        self._prompt_version = prompt_version

    def extract(
        self, current: ProjectMetadata, user_message: str, assistant_message: str
    ) -> ProjectMetadata:
        system, user = render_metadata_extractor_prompt(
            current=current,
            user_message=user_message,
            assistant_message=assistant_message,
            version=self._prompt_version,
        )
        return self._gateway.complete_structured_messages(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            ProjectMetadata,
            temperature=0,
            max_tokens=self._max_tokens,
        )

    def update(
        self, current: ProjectMetadata, user_message: str, assistant_message: str
    ) -> ProjectMetadata:
        """Bloqueante (ejecutar en threadpool). Nunca lanza: ante cualquier
        fallo devuelve el metadata actual."""
        try:
            extracted = self.extract(current, user_message, assistant_message)
        except Exception as exc:  # el extractor no puede romper la estimación
            log.warning("metadata.extraction_failed", error_type=type(exc).__name__)
            return current
        merged = merge_metadata(current, extracted)
        log.info(
            "metadata.updated",
            changed=merged != current,
            technologies=len(merged.mentioned_technologies),
            has_project_name=merged.project_name is not None,
            has_agreed_scope=merged.agreed_scope is not None,
        )
        return merged
