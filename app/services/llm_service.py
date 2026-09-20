"""Servicio LLM: construye el prompt CAG (contexto + transcripción) y llama
al proveedor configurado (OpenAI o Anthropic) para generar una estimación.
"""

from collections.abc import Iterator

from anthropic import Anthropic
from openai import OpenAI

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES

SYSTEM_PROMPT_HEADER = """Eres un estimador de software experto. Tu trabajo es generar \
estimaciones de esfuerzo (horas, desglose de tareas, equipo recomendado y \
duración) a partir de la transcripción de una reunión con un cliente.

A continuación tienes ejemplos de estimaciones reales generadas previamente. \
Úsalos como referencia de formato, nivel de detalle y criterio de estimación: \
mantén el mismo estilo (secciones, desglose numerado de tareas con horas, \
total, equipo recomendado, duración estimada y riesgos/supuestos) al generar \
la nueva estimación.
"""

SYSTEM_PROMPT_FOOTER = """Cuando el usuario te envíe la transcripción de una nueva reunión, \
genera una estimación siguiendo el mismo formato que los ejemplos anteriores. \
Basa el desglose de tareas y las horas en lo que realmente se pide en la \
transcripción, no copies los ejemplos literalmente.
"""


def _format_examples(examples: list[dict[str, str]]) -> str:
    """Formatea los ejemplos few-shot para inyectarlos en el system prompt."""
    blocks = []
    for i, example in enumerate(examples, start=1):
        blocks.append(
            f"### Ejemplo {i}\n\n"
            f"**Transcripción (resumen):** {example['meeting_summary']}\n\n"
            f"**Estimación generada:**\n{example['estimation']}"
        )
    return "\n\n---\n\n".join(blocks)


def build_system_prompt() -> str:
    """Construye el system prompt: rol del modelo + ejemplos de contexto (CAG)."""
    examples_block = _format_examples(ESTIMATION_EXAMPLES)
    return f"{SYSTEM_PROMPT_HEADER}\n{examples_block}\n\n{SYSTEM_PROMPT_FOOTER}"


def _call_openai(system_prompt: str, meeting_transcript: str) -> str:
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": meeting_transcript},
        ],
    )
    return response.choices[0].message.content


def _call_anthropic(system_prompt: str, meeting_transcript: str) -> str:
    extra_headers = {}
    if settings.anthropic_workspace_id:
        extra_headers["anthropic-workspace-id"] = settings.anthropic_workspace_id

    client = Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=system_prompt,
        messages=[
            {"role": "user", "content": meeting_transcript},
        ],
        extra_headers=extra_headers,
    )
    return response.content[0].text


def generate_estimation(meeting_transcript: str) -> str:
    """Genera una estimación a partir de la transcripción de una reunión.

    Estructura de mensajes enviada al proveedor:
        [system]    -> build_system_prompt() (instrucciones + ejemplos CAG)
        [user]      -> meeting_transcript
        [assistant] -> respuesta del modelo (lo que devuelve esta función)
    """
    system_prompt = build_system_prompt()

    if settings.llm_provider == "openai":
        return _call_openai(system_prompt, meeting_transcript)
    if settings.llm_provider == "anthropic":
        return _call_anthropic(system_prompt, meeting_transcript)

    raise ValueError(f"Proveedor LLM no soportado: {settings.llm_provider}")


def _call_openai_stream(system_prompt: str, meeting_transcript: str) -> Iterator[str]:
    client = OpenAI(api_key=settings.openai_api_key)
    stream = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": meeting_transcript},
        ],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def _call_anthropic_stream(system_prompt: str, meeting_transcript: str) -> Iterator[str]:
    extra_headers = {}
    if settings.anthropic_workspace_id:
        extra_headers["anthropic-workspace-id"] = settings.anthropic_workspace_id

    client = Anthropic(api_key=settings.anthropic_api_key)
    with client.messages.stream(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=system_prompt,
        messages=[
            {"role": "user", "content": meeting_transcript},
        ],
        extra_headers=extra_headers,
    ) as stream:
        yield from stream.text_stream


def generate_estimation_stream(meeting_transcript: str) -> Iterator[str]:
    """Genera una estimación en streaming, token a token, usando el modo
    streaming nativo de la API del proveedor configurado (SSE), no una
    simulación sobre una respuesta ya completa.
    """
    system_prompt = build_system_prompt()

    if settings.llm_provider == "openai":
        return _call_openai_stream(system_prompt, meeting_transcript)
    if settings.llm_provider == "anthropic":
        return _call_anthropic_stream(system_prompt, meeting_transcript)

    raise ValueError(f"Proveedor LLM no soportado: {settings.llm_provider}")
