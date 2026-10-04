"""Carga y renderizado de prompts versionados (Jinja2).

Los prompts viven en app/prompts/<nombre>/<versión>/*.j2. Cambiar de versión
es crear una carpeta nueva (p. ej. estimation/v2/) y pasar `version="v2"`
(o PROMPT_VERSION=v2 en la configuración): el resto del código no cambia.
"""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from app.schemas import DEFAULT_MIN_CONFIDENCE_PCT, OUT_OF_SCOPE_PREFIX, EstimationRequest

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


def render_estimation_prompt(
    request: EstimationRequest,
    version: str = DEFAULT_VERSION,
    *,
    min_confidence_pct: int = DEFAULT_MIN_CONFIDENCE_PCT,
) -> tuple[str, str]:
    """Devuelve (system, user) listos para enviar al modelo como mensajes
    separados con role "system" y role "user"."""
    prompt_dir = _template_dir("estimation", version)
    context = {
        "prompt_dir": prompt_dir,
        # Compartidos con los validadores de EstimationResult (una sola
        # definición): las versiones con sección de alcance los usan.
        "out_of_scope_prefix": OUT_OF_SCOPE_PREFIX,
        "min_confidence_pct": min_confidence_pct,
        "description": _sanitize_user_text(request.description),
        "project_type": request.project_type.value,
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
    }
    try:
        system = _env.get_template(f"{prompt_dir}/system.j2").render(context)
        user = _env.get_template(f"{prompt_dir}/user.j2").render(context)
    except TemplateNotFound as exc:
        raise PromptVersionNotFoundError(
            f"No existe la versión de prompt {version!r}"
        ) from exc
    return system.strip(), user.strip()
