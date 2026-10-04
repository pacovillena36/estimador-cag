"""Funciones puras del caché: bucket determinista y normalización."""

import pytest

from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType
from app.semantic_cache.bucket import build_bucket_key
from app.semantic_cache.normalization import normalize_description

REQUEST = EstimationRequest(
    description="Portal interno para reservar salas y puestos de trabajo.",
    project_type=ProjectType.INTERNAL_TOOL,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)


def bucket(request=REQUEST, prompt_version="v3", tenant_id="t1") -> str:
    return build_bucket_key(request, prompt_version=prompt_version, tenant_id=tenant_id)


def test_bucket_is_deterministic_and_a_sha256():
    assert bucket() == bucket()
    assert len(bucket()) == 64 and all(c in "0123456789abcdef" for c in bucket())


@pytest.mark.parametrize(
    "changed",
    [
        lambda: bucket(prompt_version="v4"),
        lambda: bucket(tenant_id="t2"),
        lambda: bucket(REQUEST.model_copy(update={"project_type": ProjectType.WEB_SAAS})),
        lambda: bucket(REQUEST.model_copy(update={"detail_level": DetailLevel.DETAILED})),
        lambda: bucket(REQUEST.model_copy(update={"output_format": OutputFormat.NARRATIVE})),
    ],
)
def test_any_component_changes_the_bucket(changed):
    assert changed() != bucket()


def test_description_does_not_change_the_bucket():
    """La descripción va por la parte vectorial, no por el bucket."""
    other = REQUEST.model_copy(update={"description": "Otra descripción totalmente distinta."})
    assert bucket(other) == bucket()


def test_bucket_does_not_expose_identifiers():
    assert "t1" not in bucket() and "v3" not in bucket()


def test_components_cannot_be_confused_by_concatenation():
    # "a|b" + "c" no debe colisionar con "a" + "b|c".
    assert bucket(prompt_version="v3", tenant_id="x|y") != bucket(prompt_version="v3|x", tenant_id="y")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Portal   de\n\nreservas\t ", "Portal de reservas"),
        ("Ｐｏｒｔａｌ de reservas", "Portal de reservas"),  # fullwidth -> NFKC
        ("Portal de reservas", "Portal de reservas"),  # espacio no separable
        ("API REST con OAuth", "API REST con OAuth"),  # no cambia mayúsculas
    ],
)
def test_normalize_description(raw, expected):
    assert normalize_description(raw) == expected
