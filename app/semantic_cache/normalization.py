"""Normalización de la descripción antes de calcular su embedding.

Distinta de app/guardrails/normalize.py a propósito: aquí no se pasa a
minúsculas (el modelo de embeddings ya lo tolera y las mayúsculas pueden
aportar significado, p. ej. siglas); solo se eliminan diferencias que no
cambian el significado y harían variar el vector.
"""

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize_description(text: str) -> str:
    """NFKC y espacios en blanco (incluidos saltos de línea) colapsados."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()
