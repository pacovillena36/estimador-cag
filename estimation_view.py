"""Presentación de una estimación en el cliente Streamlit.

El servicio IA devuelve siempre la estimación estructurada
(EstimationResult); cómo se pinta lo decide el cliente. Añadir un formato
nuevo es añadir una función aquí, sin tocar el servicio IA. Son funciones
puras (EstimationResult -> Markdown) para poder probarlas sin Streamlit.

La salida del LLM no es de confianza: todo el texto que viene del modelo
(summary, nombres de fase, asunciones) se escapa con `plain()` antes de
meterlo en el Markdown, para que se muestre como texto plano y no pueda
inyectar enlaces, imágenes, encabezados ni HTML.
"""

import re
from collections.abc import Callable

from app.schemas import DetailLevel, EstimationResult, OutputFormat, Phase


def _eur(amount: int) -> str:
    return f"{amount:,} €".replace(",", ".")


def _confidence_label(pct: int) -> str:
    if pct >= 80:
        return "alta"
    if pct >= 65:
        return "media"
    return "baja"


def _show_assumptions(detail_level: DetailLevel) -> bool:
    return detail_level != DetailLevel.SUMMARY


# Puntuación ASCII con significado en Markdown o en Streamlit ($ para LaTeX,
# ":" para emojis y colores). Escapada con "\", se muestra literalmente.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")


def plain(text: str) -> str:
    """Texto del modelo como texto plano dentro de Markdown: escapa la
    puntuación especial y aplana los saltos de línea."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def phases_table(result: EstimationResult, detail_level: DetailLevel) -> str:
    with_assumptions = _show_assumptions(detail_level)
    header = ["Fase", "Horas", "Semanas", "Coste", "Confianza"]
    if with_assumptions:
        header.append("Asunciones")
    rows = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for phase in result.phases:
        cells = [
            plain(phase.name),
            str(phase.hours),
            str(phase.duration_weeks),
            _eur(phase.cost_eur),
            f"{phase.confidence_pct} %",
        ]
        if with_assumptions:
            cells.append(plain("; ".join(phase.assumptions)) or "—")
        rows.append("| " + " | ".join(cells) + " |")
    totals = [
        "**Total**",
        f"**{result.total_hours}**",
        f"**{result.total_duration_weeks}**",
        f"**{_eur(result.total_cost_eur)}**",
        f"{result.confidence_pct} %",
    ]
    if with_assumptions:
        totals.append("")
    rows.append("| " + " | ".join(totals) + " |")
    return "\n".join([plain(result.summary), "", *rows])


def line_items(result: EstimationResult, detail_level: DetailLevel) -> str:
    lines = [plain(result.summary), ""]
    for index, phase in enumerate(result.phases, start=1):
        lines.append(
            f"{index}. **{plain(phase.name)}**: {phase.hours} h · {phase.duration_weeks} sem · "
            f"{_eur(phase.cost_eur)} · confianza {phase.confidence_pct} %"
        )
        if _show_assumptions(detail_level):
            lines.extend(f"    - {plain(assumption)}" for assumption in phase.assumptions)
    lines += [
        "",
        f"**Total: {result.total_hours} h · {result.total_duration_weeks} semanas · "
        f"{_eur(result.total_cost_eur)}**",
    ]
    return "\n".join(lines)


def _phase_paragraph(phase: Phase, detail_level: DetailLevel) -> str:
    text = (
        f"La fase de **{plain(phase.name)}** requiere unas {phase.hours} horas en "
        f"{phase.duration_weeks} semanas ({_eur(phase.cost_eur)}), con una confianza "
        f"{_confidence_label(phase.confidence_pct)}."
    )
    if _show_assumptions(detail_level) and phase.assumptions:
        text += " Asumimos que " + " y que ".join(plain(a) for a in phase.assumptions) + "."
    return text


def narrative(result: EstimationResult, detail_level: DetailLevel) -> str:
    paragraphs = [plain(result.summary)]
    paragraphs += [_phase_paragraph(phase, detail_level) for phase in result.phases]
    paragraphs.append(
        f"En total, el proyecto suma unas {result.total_hours} horas en "
        f"{result.total_duration_weeks} semanas, con un coste de "
        f"{_eur(result.total_cost_eur)} y una confianza global "
        f"{_confidence_label(result.confidence_pct)}."
    )
    return "\n\n".join(paragraphs)


RENDERERS: dict[OutputFormat, Callable[[EstimationResult, DetailLevel], str]] = {
    OutputFormat.PHASES_TABLE: phases_table,
    OutputFormat.LINE_ITEMS: line_items,
    OutputFormat.NARRATIVE: narrative,
}


def render(
    result: EstimationResult, output_format: OutputFormat, detail_level: DetailLevel
) -> str:
    return RENDERERS[output_format](result, detail_level)
