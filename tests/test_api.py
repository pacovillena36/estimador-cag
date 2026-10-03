import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.llm_gateway import get_llm_gateway

VALID_REQUEST = {
    "description": "Portal interno para reservar salas y puestos de trabajo.",
    "project_type": "internal_tool",
    "detail_level": "medium",
    "output_format": "phases_table",
}


@pytest.fixture
def client(gateway, fake_completion):
    # Sin `with`: no se ejecuta el lifespan, así que no hacen falta claves reales.
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_estimate_returns_text_and_prompt_version(client):
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 200
    assert response.json() == {"text": "Estimación: 40 horas", "prompt_version": "v1"}


def test_model_receives_separate_system_and_user_messages(client, fake_completion):
    client.post("/api/v1/estimate", json=VALID_REQUEST)

    system, user = fake_completion.last_messages
    assert system["role"] == "system"
    assert user["role"] == "user"
    assert VALID_REQUEST["description"] in user["content"]
    assert VALID_REQUEST["description"] not in system["content"]
    assert "confidence_pct" in system["content"]


def test_form_options_change_the_prompt(client, fake_completion):
    client.post("/api/v1/estimate", json=VALID_REQUEST)
    table_system = fake_completion.last_messages[0]["content"]

    client.post("/api/v1/estimate", json=VALID_REQUEST | {"output_format": "narrative"})
    narrative_system = fake_completion.last_messages[0]["content"]

    assert table_system != narrative_system
    # Opciones distintas -> prompt distinto -> no comparten entrada de caché.
    assert fake_completion.calls == ["anthropic", "anthropic"]



def test_prompt_version_query_param_selects_the_template(client, fake_completion):
    response = client.post("/api/v1/estimate?prompt_version=v2", json=VALID_REQUEST)
    assert response.status_code == 200
    assert response.json()["prompt_version"] == "v2"
    v2_system = fake_completion.last_messages[0]["content"]

    client.post("/api/v1/estimate", json=VALID_REQUEST)
    v1_system = fake_completion.last_messages[0]["content"]

    assert "fisioterapia" in v2_system and "fisioterapia" not in v1_system
    # Versión distinta -> prompt distinto -> no comparten entrada de caché.
    assert fake_completion.calls == ["anthropic", "anthropic"]


def test_stream_accepts_prompt_version(client):
    response = client.post("/api/v1/estimate/stream?prompt_version=v2", json=VALID_REQUEST)
    event, done = _sse_events(response.text)[-1]
    assert event == "done"
    assert done["prompt_version"] == "v2"


@pytest.mark.parametrize("path", ["/api/v1/estimate", "/api/v1/estimate/stream"])
@pytest.mark.parametrize("version", ["v99", "latest", "../v1", ""])
def test_unknown_prompt_version_is_rejected_without_calling_the_model(
    client, fake_completion, path, version
):
    response = client.post(f"{path}?prompt_version={version}", json=VALID_REQUEST)
    assert response.status_code == 422
    assert fake_completion.calls == []

@pytest.mark.parametrize(
    "override",
    [
        {"description": "Muy corta"},  # < 20 caracteres
        {"description": "a" * 2001},  # > 2000 caracteres
        {"description": "   " + "x" * 5 + "   "},  # < 20 tras recortar espacios
        {"project_type": "videojuego"},
        {"detail_level": "extremo"},
        {"output_format": "pdf"},
    ],
)
def test_invalid_requests_are_rejected(client, override):
    response = client.post("/api/v1/estimate", json=VALID_REQUEST | override)
    assert response.status_code == 422


def test_estimate_returns_503_without_internal_details(client, fake_completion):
    fake_completion.failing = {"anthropic", "openai"}
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "proveedor caído" not in response.text


def test_estimate_rejects_empty_model_output(client, fake_completion):
    fake_completion.text = "   "
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 502


def test_surrounding_whitespace_does_not_break_cache(client, fake_completion):
    padded = VALID_REQUEST | {"description": "\n  " + VALID_REQUEST["description"] + " \n"}
    client.post("/api/v1/estimate", json=VALID_REQUEST)
    client.post("/api/v1/estimate", json=padded)
    assert fake_completion.calls == ["anthropic"]


def test_stream_emits_deltas_then_done(client, fake_completion):
    fake_completion.failing = {"anthropic"}
    response = client.post("/api/v1/estimate/stream", json=VALID_REQUEST)
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "".join(d["delta"] for e, d in events if e == "delta") == "Estimación: 40 horas"
    event, done = events[-1]
    assert event == "done"
    assert done["provider"] == "openai"
    assert done["prompt_version"] == "v1"


def test_stream_emits_error_event_when_interrupted(client, fake_completion):
    fake_completion.fail_mid_stream = {"anthropic"}
    response = client.post("/api/v1/estimate/stream", json=VALID_REQUEST)
    events = _sse_events(response.text)
    assert events[-1][0] == "error"
    assert "conexión perdida" not in response.text


def test_request_id_is_generated_or_propagated(client):
    assert client.get("/health").headers["x-request-id"]
    propagated = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert propagated.headers["x-request-id"] == "abc-123"
    sanitized = client.get("/health", headers={"X-Request-ID": "bad id <script>"})
    assert sanitized.headers["x-request-id"] != "bad id <script>"
