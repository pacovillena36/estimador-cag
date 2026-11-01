"""Anclas heurísticas: frases de la transcripción que no se pueden perder.

Sin LLM: reglas deterministas sobre cada frase de la transcripción del
usuario (no de los adjuntos, que inundarían la lista). Una frase es ancla si
contiene un presupuesto, un plazo, el tamaño del equipo, una restricción o
una decisión tecnológica. Se guardan literales (recortadas), así un dato como
"el presupuesto máximo es 40.000 €" sobrevive aunque su turno salga de la
ventana y aunque el resumen lo parafrasee mal.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

MAX_ANCHOR_CHARS = 300


class AnchorKind(StrEnum):
    BUDGET = "budget"
    DEADLINE = "deadline"
    TEAM = "team"
    CONSTRAINT = "constraint"
    TECH_DECISION = "tech_decision"


@dataclass(frozen=True)
class Anchor:
    kind: AnchorKind
    text: str
    turn: int

    @property
    def key(self) -> str:
        """Clave de deduplicación: sin mayúsculas ni espacios repetidos."""
        return " ".join(self.text.casefold().split())


_MONTHS = r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre"

# El orden importa: una frase se clasifica con la primera regla que cumple.
_RULES: tuple[tuple[AnchorKind, re.Pattern[str]], ...] = (
    (
        AnchorKind.BUDGET,
        re.compile(
            r"presupuesto|budget|coste m[aá]ximo|\d[\d.,]*\s*(?:k\b|mil\b|€|eur\b|euros|usd|\$|d[oó]lares)",
            re.IGNORECASE,
        ),
    ),
    (
        AnchorKind.TEAM,
        re.compile(
            r"equipo de \d+|\b\d+\s*(?:personas|desarrolladores|devs|developers|ingenier[oa]s)\b",
            re.IGNORECASE,
        ),
    ),
    (
        AnchorKind.DEADLINE,
        re.compile(
            rf"plazo|deadline|fecha l[ií]mite|antes de (?:{_MONTHS}|fin de)|"
            rf"\b\d+\s*(?:semanas?|meses|mes|d[ií]as|weeks?|months?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        AnchorKind.CONSTRAINT,
        re.compile(
            r"obligatori|imprescindible|requisito|no incluye|fuera del alcance|"
            r"\bdebe(?:mos|n)?\b|\bmust\b|\brequired\b",
            re.IGNORECASE,
        ),
    ),
    (
        AnchorKind.TECH_DECISION,
        re.compile(
            r"usaremos|vamos a usar|hemos decidido|decidimos|hemos elegido|elegimos|"
            r"el stack|nuestro stack|en la nube de",
            re.IGNORECASE,
        ),
    ),
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _clip(sentence: str) -> str:
    if len(sentence) <= MAX_ANCHOR_CHARS:
        return sentence
    return sentence[: MAX_ANCHOR_CHARS - 1].rstrip() + "…"


def extract_anchors(transcript: str, turn: int) -> tuple[Anchor, ...]:
    """Anclas de la transcripción de un turno, sin duplicados y en orden."""
    anchors: list[Anchor] = []
    seen: set[str] = set()
    for sentence in _sentences(transcript):
        for kind, pattern in _RULES:
            if pattern.search(sentence):
                anchor = Anchor(kind=kind, text=_clip(sentence), turn=turn)
                if anchor.key not in seen:
                    seen.add(anchor.key)
                    anchors.append(anchor)
                break
    return tuple(anchors)
