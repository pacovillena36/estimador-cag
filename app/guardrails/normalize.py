"""Normalización de texto previa a las heurísticas.

Las heurísticas (injection, PII) se aplican sobre el texto normalizado para
no caer ante bypasses triviales: mayúsculas, caracteres de ancho completo
(ＩＧＮＯＲＥ), caracteres invisibles (ig​nore) o espacios repetidos.
"""

import re
import unicodedata

# Caracteres de ancho cero y similares que no se ven pero rompen una regex.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFKC + casefold, sin caracteres invisibles y con los espacios en
    blanco colapsados a uno solo."""
    text = unicodedata.normalize("NFKC", text.translate(_INVISIBLE)).casefold()
    return _WHITESPACE.sub(" ", text).strip()
