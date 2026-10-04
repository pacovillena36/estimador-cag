"""Heurísticas de prompt injection (capa 2, política EXCEPTION).

Arranca en LOG_ONLY: algunos patrones producen falsos positivos legítimos
(p. ej. "system prompt" en la descripción de un proyecto de chatbot). Se
observan sus disparos en los logs antes de pasar a ENFORCE.

Es una defensa más, no la única: aunque esté en LOG_ONLY, el loader de
prompts neutraliza "<" y ">" de la descripción, y el system prompt indica
tratar <project_description> solo como datos.
"""

import re

from app.guardrails.base import FailurePolicy, GuardrailResult

NAME = "prompt_injection"

# Única fuente de patrones: (identificador para logs, regex). Se aplican
# sobre texto normalizado (minúsculas, sin invisibles, espacios colapsados).
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (pattern_id, re.compile(regex))
    for pattern_id, regex in (
        # Inglés
        ("ignore_previous", r"\b(ignore|disregard|forget) (all )?(the )?(previous|prior|above|earlier)\b"),
        ("ignore_instructions", r"\b(ignore|disregard|forget) (all|any|your|the) (instructions|rules)\b"),
        ("you_are_now", r"\byou are now\b"),
        ("system_prompt", r"\bsystem prompt\b"),
        # Español
        ("ignora_instrucciones", r"\b(ignora|olvida) (todas )?(las |tus )?(instrucciones|indicaciones|reglas)\b"),
        ("ignora_lo_anterior", r"\b(ignora|olvida) (todo )?lo (anterior|de antes)\b"),
        ("a_partir_de_ahora", r"\ba partir de ahora (eres|act[uú]as|ser[aá]s)\b"),
        ("prompt_del_sistema", r"\bprompt (del|de) sistema\b"),
        # Etiquetas de rol o de cierre del delimitador inyectadas
        ("role_tag", r"<\s*/?\s*(system|assistant|user|developer|instructions?|project_description)\b[^>]*>"),
    )
)


def detect_prompt_injection(normalized_text: str) -> GuardrailResult:
    matched = [pid for pid, pattern in INJECTION_PATTERNS if pattern.search(normalized_text)]
    return GuardrailResult(
        name=NAME,
        triggered=bool(matched),
        policy=FailurePolicy.EXCEPTION,
        internal_detail=",".join(matched) or None,
    )
