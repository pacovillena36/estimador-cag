"""Carga y renderizado de prompts versionados (Jinja2).

Los prompts viven en app/prompts/<nombre>/<versión>/*.j2. Cambiar de versión
es crear una carpeta nueva (p. ej. estimation/v2/) y pasar `version="v2"`
(o PROMPT_VERSION=v2 en la configuración): el resto del código no cambia.
"""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from app.schemas import (
    DEFAULT_MIN_CONFIDENCE_PCT,
    OUT_OF_SCOPE_PREFIX,
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
)
from app.sessions import ProjectMetadata

PROMPTS_DIR = Path(__file__).parent
DEFAULT_VERSION = "v1"

# Solo nombres de versión tipo "v1", "v2"...: evita que un valor de
# configuración se use para leer rutas arbitrarias del disco.
_VERSION_RE = re.compile(r"^v\d+$")

# La descripción va delimitada por <project_description> en user.j2. Se
# neutralizan TODOS los "<" y ">" (por comillas angulares, que no forman
# etiquetas) para que el usuario no pueda cerrar el bloque ni inyectar
# etiquetas de rol (<system>...), aunque el detector de prompt injection
# esté en LOG_ONLY.
_ANGLE_BRACKETS = str.maketrans({"<": "‹", ">": "›"})

# Etiquetas que delimitan bloques en los prompts de sesión. Si aparecen en
# contenido de terceros (transcripción, adjuntos, metadata generada por el
# LLM), se neutralizan para que no puedan "cerrar" su bloque.
_SESSION_DELIMITERS = re.compile(
    r"<\s*/?\s*(transcript|attachment_content|project_metadata|conversation_turn|assistant_estimate)\b[^>]*>",
    re.IGNORECASE,
)


def neutralize_session_delimiters(text: str) -> str:
    return _SESSION_DELIMITERS.sub("[etiqueta eliminada]", text)

_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    # Una variable que falte en el template es un error, no un hueco vacío
    # silencioso en el prompt.
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    # Los prompts son texto plano, no HTML: escapar convertiría comillas o
    # "<" de la descripción en entidades HTML y cambiaría lo que ve el modelo.
    autoescape=False,
)
# `tojson` (ejemplos few-shot): JSON legible, con acentos tal cual y en el
# orden de los campos del contrato, no ordenado alfabéticamente.
_env.policies["json.dumps_kwargs"] = {"ensure_ascii": False, "indent": 2}


class PromptVersionNotFoundError(LookupError):
    """La versión de prompt pedida no existe o no es válida."""


def _template_dir(name: str, version: str) -> str:
    if not _VERSION_RE.match(version):
        raise PromptVersionNotFoundError(f"Versión de prompt no válida: {version!r}")
    return f"{name}/{version}"


def _sanitize_user_text(text: str) -> str:
    return text.translate(_ANGLE_BRACKETS)


def validate_estimation_prompt_version(version: str = DEFAULT_VERSION) -> None:
    """Comprueba que existen los templates de la versión (para fallar al
    arrancar la API y no en la primera petición)."""
    prompt_dir = _template_dir("estimation", version)
    for name in ("system.j2", "user.j2", "examples.j2"):
        try:
            _env.get_template(f"{prompt_dir}/{name}")
        except TemplateNotFound as exc:
            raise PromptVersionNotFoundError(
                f"Falta el template {prompt_dir}/{name}"
            ) from exc


def _context(
    prompt_dir: str,
    *,
    project_type: ProjectType,
    detail_level: DetailLevel,
    output_format: OutputFormat,
    min_confidence_pct: int,
    project_metadata: ProjectMetadata | None = None,
) -> dict:
    return {
        "prompt_dir": prompt_dir,
        # Compartidos con los validadores de EstimationResult (una sola
        # definición): las versiones con sección de alcance los usan.
        "out_of_scope_prefix": OUT_OF_SCOPE_PREFIX,
        "min_confidence_pct": min_confidence_pct,
        "project_type": project_type.value,
        "detail_level": detail_level.value,
        "output_format": output_format.value,
        # Las versiones con <project_metadata> (v4+) reciben el metadata
        # serializado como JSON, nunca campos sueltos interpolados: un valor
        # con saltos de línea o etiquetas no puede alterar el prompt.
        "project_metadata": project_metadata,
        "project_metadata_json": (
            neutralize_session_delimiters(
                project_metadata.model_dump_json(exclude_none=True, indent=2)
            )
            if project_metadata is not None
            else ""
        ),
    }


def render_session_system_prompt(
    *,
    project_type: ProjectType,
    detail_level: DetailLevel,
    output_format: OutputFormat,
    project_metadata: ProjectMetadata,
    version: str,
    min_confidence_pct: int = DEFAULT_MIN_CONFIDENCE_PCT,
) -> str:
    """System prompt de un turno de sesión, regenerado en cada llamada con
    el project_metadata actual (no se guarda en el historial)."""
    prompt_dir = _template_dir("estimation", version)
    context = _context(
        prompt_dir,
        project_type=project_type,
        detail_level=detail_level,
        output_format=output_format,
        min_confidence_pct=min_confidence_pct,
        project_metadata=project_metadata,
    )
    try:
        return _env.get_template(f"{prompt_dir}/system.j2").render(context).strip()
    except TemplateNotFound as exc:
        raise PromptVersionNotFoundError(
            f"No existe la versión de prompt {version!r}"
        ) from exc


def render_estimation_prompt(
    request: EstimationRequest,
    version: str = DEFAULT_VERSION,
    *,
    min_confidence_pct: int = DEFAULT_MIN_CONFIDENCE_PCT,
) -> tuple[str, str]:
    """Devuelve (system, user) listos para enviar al modelo como mensajes
    separados con role "system" y role "user"."""
    prompt_dir = _template_dir("estimation", version)
    context = _context(
        prompt_dir,
        project_type=request.project_type,
        detail_level=request.detail_level,
        output_format=request.output_format,
        min_confidence_pct=min_confidence_pct,
    ) | {"description": _sanitize_user_text(request.description)}
    try:
        system = _env.get_template(f"{prompt_dir}/system.j2").render(context)
        user = _env.get_template(f"{prompt_dir}/user.j2").render(context)
    except TemplateNotFound as exc:
        raise PromptVersionNotFoundError(
            f"No existe la versión de prompt {version!r}"
        ) from exc
    return system.strip(), user.strip()


def render_metadata_extractor_prompt(
    *,
    current: ProjectMetadata,
    user_message: str,
    assistant_message: str,
    version: str = "v1",
) -> tuple[str, str]:
    """(system, user) del extractor de project_metadata
    (app/prompts/metadata/<versión>/extractor.j2 y extractor_user.j2)."""
    prompt_dir = _template_dir("metadata", version)
    context = {
        "current_metadata_json": neutralize_session_delimiters(
            current.model_dump_json(exclude_none=True, indent=2)
        ),
        # El mensaje de usuario ya viene neutralizado (compose_user_message);
        # la respuesta del asistente es salida del LLM: también se neutraliza.
        "user_message": neutralize_session_delimiters(user_message),
        "assistant_message": neutralize_session_delimiters(assistant_message),
    }
    try:
        system = _env.get_template(f"{prompt_dir}/extractor.j2").render(context)
        user = _env.get_template(f"{prompt_dir}/extractor_user.j2").render(context)
    except TemplateNotFound as exc:
        raise PromptVersionNotFoundError(f"No existe el prompt de metadata {version!r}") from exc
    return system.strip(), user.strip()
