"""Presentación en el cliente: el mismo EstimationResult se pinta como
tabla, lista o narrativa según el formato elegido en el formulario."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.schemas import (
    DetailLevel,
    EstimationRequest,
    EstimationResponse,
    EstimationResult,
    OutputFormat,
    ProjectType,
)
from estimation_view import render
from tests.conftest import VALID_RESULT

APP_PATH = Path(__file__).parent.parent / "streamlit_app.py"
RESULT = EstimationResult.model_validate(VALID_RESULT)


def test_phases_table_has_one_row_per_phase_plus_header_and_total():
    text = render(RESULT, OutputFormat.PHASES_TABLE, DetailLevel.MEDIUM)
    table = [line for line in text.splitlines() if line.startswith("|")]
    assert len(table) == 2 + len(RESULT.phases) + 1
    assert "Asunciones" in table[0]
    assert "**6.600 €**" in table[-1]


def test_summary_detail_hides_assumptions():
    for output_format in OutputFormat:
        detailed = render(RESULT, output_format, DetailLevel.DETAILED)
        summary = render(RESULT, output_format, DetailLevel.SUMMARY)
        assert "guía de marca" in detailed
        assert "guía de marca" not in summary


def test_line_items_and_narrative():
    items = render(RESULT, OutputFormat.LINE_ITEMS, DetailLevel.MEDIUM)
    assert "1. **Diseño**: 40 h · 2 sem · 2.200 €" in items
    assert "**Total: 120 h · 6 semanas · 6.600 €**" in items

    prose = render(RESULT, OutputFormat.NARRATIVE, DetailLevel.MEDIUM)
    assert "|" not in prose
    assert "confianza alta" in prose  # fase Diseño, 80 %


def test_pipes_in_model_text_do_not_break_the_table():
    result = RESULT.model_copy(
        update={"phases": [RESULT.phases[0].model_copy(update={"name": "A|B"}), RESULT.phases[1]]}
    )
    assert "A\\|B" in render(result, OutputFormat.PHASES_TABLE, DetailLevel.SUMMARY)


@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_streamlit_app_renders_the_last_estimation(output_format):
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["last_estimation"] = {
        "request": EstimationRequest(
            description="Portal interno para reservar salas y puestos de trabajo.",
            project_type=ProjectType.INTERNAL_TOOL,
            detail_level=DetailLevel.MEDIUM,
            output_format=output_format,
        ),
        "response": EstimationResponse(result=RESULT, prompt_version="v1"),
    }
    app.run()

    assert not app.exception
    assert [m.value for m in app.metric] == ["120", "6", "6.600 €", "75 %"]
    assert any(RESULT.summary in md.value for md in app.markdown)
