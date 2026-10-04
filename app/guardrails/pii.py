"""Detección y redacción de PII: emails, teléfonos e IBAN.

- Input (capa 2): política EXCEPTION, modo LOG_ONLY por defecto.
- Output (capa 5): política FILTER, modo LOG_ONLY por defecto; en ENFORCE
  se redactan las coincidencias con [REDACTED] en lugar de abortar.

AVISO: la detección por regex es frágil. Se escapan formatos poco comunes
(teléfonos escritos con letras, emails ofuscados como "nombre arroba
dominio") y puede haber falsos positivos con números largos. Si el dominio
lo exige (datos de salud, cumplimiento normativo), sustitúyela por un
detector dedicado (p. ej. Microsoft Presidio o un servicio de DLP).
Los IBAN se validan con su checksum (mod 97) para reducir falsos positivos.

Nunca se registran los valores detectados: solo sus categorías.
"""

import re
from dataclasses import dataclass

from app.guardrails.base import FailurePolicy, GuardrailResult

REDACTED = "[REDACTED]"

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}", re.IGNORECASE)
# Teléfono español: 9 dígitos empezando por 6, 7, 8 o 9, con prefijo +34 /
# 0034 opcional y separadores (espacio, punto o guion) entre dígitos.
_PHONE_ES = re.compile(r"(?<![\w+])(?:(?:\+|00)34[\s.-]?)?[6789](?:[\s.-]?\d){8}(?!\d)")
# Internacional: prefijo + o 00, código de país y 6 a 12 dígitos más.
_PHONE_INTL = re.compile(r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}(?:[\s.-]?\d){6,12}(?!\d)")
# Inicio candidato de IBAN: 2 letras de país + 2 dígitos de control.
_IBAN_START = re.compile(r"\b[a-z]{2}\d{2}", re.IGNORECASE)
_IBAN_MIN, _IBAN_MAX = 15, 34


@dataclass(frozen=True)
class PiiMatch:
    category: str
    start: int
    end: int


def iban_is_valid(iban: str) -> bool:
    """Checksum ISO 13616: mover los 4 primeros caracteres al final, pasar
    letras a números (A=10...Z=35) y comprobar que el resto mod 97 es 1."""
    compact = iban.replace(" ", "").upper()
    if not (_IBAN_MIN <= len(compact) <= _IBAN_MAX) or not compact.isalnum():
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def _find_ibans(text: str) -> list[PiiMatch]:
    """El IBAN puede ir agrupado con espacios y seguido de más texto, así
    que se prueban todas las longitudes posibles desde cada inicio candidato
    y se queda la más larga con checksum válido."""
    matches: list[PiiMatch] = []
    for start_match in _IBAN_START.finditer(text):
        start = start_match.start()
        if matches and start < matches[-1].end:
            continue
        best_end, alnum, pos = None, 0, start
        while pos < len(text) and alnum < _IBAN_MAX:
            char = text[pos]
            if char.isalnum() and char.isascii():
                alnum += 1
                pos += 1
                at_boundary = pos == len(text) or not text[pos].isalnum()
                if alnum >= _IBAN_MIN and at_boundary and iban_is_valid(text[start:pos]):
                    best_end = pos
            elif char == " " and pos + 1 < len(text) and text[pos + 1].isalnum():
                pos += 1
            else:
                break
        if best_end is not None:
            matches.append(PiiMatch("iban", start, best_end))
    return matches


def find_pii(text: str) -> list[PiiMatch]:
    """Coincidencias de PII ordenadas por posición y sin solapes."""
    found = _find_ibans(text)
    for category, pattern in (("email", _EMAIL), ("phone", _PHONE_ES), ("phone", _PHONE_INTL)):
        found += [PiiMatch(category, m.start(), m.end()) for m in pattern.finditer(text)]

    result: list[PiiMatch] = []
    for match in sorted(found, key=lambda m: (m.start, -(m.end - m.start))):
        if not result or match.start >= result[-1].end:
            result.append(match)
    return result


def redact(text: str) -> tuple[str, list[str]]:
    """Devuelve (texto con cada coincidencia sustituida por [REDACTED],
    categorías encontradas)."""
    matches = find_pii(text)
    for match in reversed(matches):
        text = text[: match.start] + REDACTED + text[match.end :]
    return text, [m.category for m in matches]


def detect_pii(text: str, *, name: str = "pii_input") -> GuardrailResult:
    categories = sorted({m.category for m in find_pii(text)})
    return GuardrailResult(
        name=name,
        triggered=bool(categories),
        policy=FailurePolicy.EXCEPTION,
        internal_detail=",".join(categories) or None,
    )
