"""Servicio LLM: construye el prompt CAG (contexto + transcripción) y llama
al proveedor configurado (OpenAI o Anthropic) para generar una estimación.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass

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


@dataclass
class StreamMetrics:
    """Metadatos de una llamada en streaming, rellenados progresivamente a
    medida que se consume el generador de texto y completos una vez agotado.
    """

    provider: str = ""
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    elapsed_seconds: float | None = None


def _call_openai_stream(
    system_prompt: str, meeting_transcript: str, metrics: StreamMetrics
) -> Iterator[str]:
    client = OpenAI(api_key=settings.openai_api_key)
    start = time.perf_counter()
    stream = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": meeting_transcript},
        ],
        stream=True,
        stream_options={"include_usage": True},
    )
    for chunk in stream:
        if chunk.usage is not None:
            metrics.input_tokens = chunk.usage.prompt_tokens
            metrics.output_tokens = chunk.usage.completion_tokens
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    metrics.provider = "openai"
    metrics.model = settings.openai_model
    metrics.elapsed_seconds = time.perf_counter() - start


def _call_anthropic_stream(
    system_prompt: str, meeting_transcript: str, metrics: StreamMetrics
) -> Iterator[str]:
    extra_headers = {}
    if settings.anthropic_workspace_id:
        extra_headers["anthropic-workspace-id"] = settings.anthropic_workspace_id

    client = Anthropic(api_key=settings.anthropic_api_key)
    start = time.perf_counter()
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
        final_message = stream.get_final_message()

    metrics.provider = "anthropic"
    metrics.model = final_message.model
    metrics.input_tokens = final_message.usage.input_tokens
    metrics.output_tokens = final_message.usage.output_tokens
    metrics.elapsed_seconds = time.perf_counter() - start


def generate_estimation_stream(
    meeting_transcript: str,
) -> tuple[Iterator[str], StreamMetrics]:
    """Genera una estimación en streaming, token a token, usando el modo
    streaming nativo de la API del proveedor configurado (SSE), no una
    simulación sobre una respuesta ya completa.

    Devuelve el iterador de texto junto con un `StreamMetrics` que se va
    rellenando (tokens, modelo, tiempo de respuesta) a medida que se agota
    el iterador; sus campos están completos una vez consumido por entero.
    """
    system_prompt = build_system_prompt()
    metrics = StreamMetrics()

    if settings.llm_provider == "openai":
        return _call_openai_stream(system_prompt, meeting_transcript, metrics), metrics
    if settings.llm_provider == "anthropic":
        return _call_anthropic_stream(system_prompt, meeting_transcript, metrics), metrics

    raise ValueError(f"Proveedor LLM no soportado: {settings.llm_provider}")
