"""Presentación en el cliente: el mismo EstimationResult se pinta como
tabla, lista o narrativa según el formato elegido en el formulario."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from app.schemas import (
    OUT_OF_SCOPE_PREFIX,
    DetailLevel,
    EstimationRequest,
    EstimationResponse,
    EstimationResult,
    OutputFormat,
    ProjectType,
)
from app.sessions import ProjectMetadata, SessionState
from estimation_view import plain, render
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


def test_model_text_cannot_inject_markdown():
    """La salida del LLM no es de confianza: se pinta como texto plano."""
    hostile = RESULT.model_copy(
        update={"summary": "Ver [aquí](http://evil.example) ![x](http://evil.example/p.png)\n# Título <b>"}
    )
    for output_format in OutputFormat:
        text = render(hostile, output_format, DetailLevel.MEDIUM)
        assert "](" not in text.replace("\\]\\(", "")
        assert "\n# " not in text and "<b>" not in text
    assert plain("a\nb") == "a b"


SESSION_ID = "6f1c1d1e-1234-4000-8000-000000000000"


def _turn(response: EstimationResponse, output_format=OutputFormat.PHASES_TABLE, attachments=()):
    # Mismos atributos que streamlit_app.Turn (la app es un script).
    return SimpleNamespace(
        transcript="Portal interno para reservar salas y puestos de trabajo.",
        attachment_names=list(attachments),
        project_type=ProjectType.INTERNAL_TOOL,
        detail_level=DetailLevel.MEDIUM,
        output_format=output_format,
        response=response,
    )


def _app_with(*turns, session_info: SessionState | None = None) -> AppTest:
    """App con una sesión ya creada (session_id en session_state): no hace
    ninguna llamada HTTP al cargar."""
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["session_id"] = SESSION_ID
    app.session_state["turns"] = list(turns)
    app.session_state["session_info"] = session_info
    app.run()
    return app


@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_streamlit_app_renders_the_last_estimation(output_format):
    app = _app_with(_turn(EstimationResponse(result=RESULT, prompt_version="v4"), output_format))

    assert not app.exception
    assert [m.value for m in app.main.metric] == ["120", "6", "6.600 €", "75 %"]
    assert any(plain(RESULT.summary) in md.value for md in app.markdown)


def test_streamlit_app_shows_out_of_scope_as_a_warning():
    out_of_scope = EstimationResult(
        summary=f"{OUT_OF_SCOPE_PREFIX} no es un proyecto de software.",
        total_hours=0,
        total_duration_weeks=0,
        total_cost_eur=0,
        confidence_pct=0,
        phases=[],
    )
    app = _app_with(_turn(EstimationResponse(result=out_of_scope, prompt_version="v4")))

    assert not app.exception
    assert len(app.main.metric) == 0
    (warning,) = app.warning
    assert "no es un proyecto de software" in warning.value


def test_streamlit_app_shows_turns_and_project_metadata_in_the_sidebar():
    response = EstimationResponse(result=RESULT, prompt_version="v4")
    info = SessionState(
        session_id=SESSION_ID,
        project_metadata=ProjectMetadata(project_name="Atlas", mentioned_technologies=["React", "Kafka"]),
        turns=2,
    )
    app = _app_with(_turn(response), _turn(response, attachments=["requisitos.pdf"]), session_info=info)

    assert not app.exception
    assert [m.value for m in app.sidebar.metric] == ["2"]
    sidebar_text = " ".join(md.value for md in app.sidebar.markdown)
    assert "Atlas" in sidebar_text and "React, Kafka" in sidebar_text
    labels = [e.label for e in app.expander]
    assert labels[0].startswith("Turno 2") and labels[1].startswith("Turno 1")
