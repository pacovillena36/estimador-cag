"""Construcción del prompt CAG de estimación (instrucciones + ejemplos).

Las llamadas a los proveedores LLM viven en app/services/llm_gateway.py;
este módulo solo sabe de dominio (qué se le pide al modelo), no de
proveedores.
"""

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
    """Construye el system prompt: rol del modelo + ejemplos de contexto (CAG).

    Estructura de mensajes que el wrapper envía al proveedor:
        [system] -> build_system_prompt() (instrucciones + ejemplos CAG)
        [user]   -> transcripción de la reunión
    """
    examples_block = _format_examples(ESTIMATION_EXAMPLES)
    return f"{SYSTEM_PROMPT_HEADER}\n{examples_block}\n\n{SYSTEM_PROMPT_FOOTER}"
