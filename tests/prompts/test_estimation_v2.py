"""Tests del template de estimación v2. La variación deliberada respecto a
v1 es solo el set de ejemplos few-shot: estos tests fijan que las
instrucciones son las mismas y que lo que cambia son los ejemplos."""

import itertools

import pytest

from app.prompts.loader import render_estimation_prompt, validate_estimation_prompt_version
from tests.prompts.test_estimation_v1 import examples_json
from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType

DESCRIPTION = (
    "Portal interno para que los empleados reserven salas y puestos de "
    "trabajo, con login corporativo y un panel de ocupación por planta."
)


def make_request(**overrides) -> EstimationRequest:
    fields = {
        "description": DESCRIPTION,
        "project_type": ProjectType.INTERNAL_TOOL,
        "detail_level": DetailLevel.MEDIUM,
        "output_format": OutputFormat.PHASES_TABLE,
    }
    return EstimationRequest(**(fields | overrides))


def split_examples(system: str) -> tuple[str, str]:
    instructions, examples = system.split("## Ejemplos", 1)
    return instructions, examples


def test_v2_templates_exist():
    validate_estimation_prompt_version("v2")


@pytest.mark.parametrize(
    ("detail_level", "output_format"),
    list(itertools.product(DetailLevel, OutputFormat)),
)
def test_v2_keeps_v1_instructions_and_user_message(detail_level, output_format):
    request = make_request(detail_level=detail_level, output_format=output_format)
    v1_system, v1_user = render_estimation_prompt(request, version="v1")
    v2_system, v2_user = render_estimation_prompt(request, version="v2")

    assert split_examples(v1_system)[0] == split_examples(v2_system)[0]
    assert v1_user == v2_user
    assert split_examples(v1_system)[1] != split_examples(v2_system)[1]


def test_v2_uses_a_different_set_of_examples():
    v1_system, _ = render_estimation_prompt(make_request(), version="v1")
    v2_system, _ = render_estimation_prompt(make_request(), version="v2")
    _, v1_examples = split_examples(v1_system)
    _, v2_examples = split_examples(v2_system)

    assert "cafeterías" in v1_examples and "cafeterías" not in v2_examples
    assert "fisioterapia" in v2_examples and "fisioterapia" not in v1_examples
    assert "SAP" in v2_examples


def test_v2_example_totals_are_the_sum_of_phases():
    system, _ = render_estimation_prompt(make_request(), version="v2")
    saas, internal = examples_json(system)
    # SaaS: 50 + 84 + 128 + 56 + 54 h a 55 €/h; interna: 32 + 92 + 72 + 16 + 44 h a 60 €/h.
    assert (saas["total_hours"], saas["total_cost_eur"]) == (372, 372 * 55)
    assert (internal["total_hours"], internal["total_cost_eur"]) == (256, 256 * 60)


def test_all_option_combinations_render_in_v2():
    for project_type, detail_level, output_format in itertools.product(
        ProjectType, DetailLevel, OutputFormat
    ):
        system, user = render_estimation_prompt(
            make_request(
                project_type=project_type,
                detail_level=detail_level,
                output_format=output_format,
            ),
            version="v2",
        )
        assert "{{" not in system + user and "{%" not in system + user
