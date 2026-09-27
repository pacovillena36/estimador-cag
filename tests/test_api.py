import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.llm_gateway import get_llm_gateway


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


def test_estimate_returns_normalized_response(client):
    response = client.post("/api/v1/estimate", json={"transcription": "Reunión X"})
    assert response.status_code == 200
    body = response.json()
    assert body["estimation"] == "Estimación: 40 horas"
    assert body["model"] == "claude-haiku-4-5"
    assert body["provider"] == "anthropic"
    assert body["cached"] is False
    assert body["finish_reason"] == "stop"
    assert body["cost_usd"] > 0


def test_estimate_returns_503_without_internal_details(client, fake_completion):
    fake_completion.failing = {"anthropic", "openai"}
    response = client.post("/api/v1/estimate", json={"transcription": "Reunión X"})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "proveedor caído" not in response.text


def test_estimate_rejects_empty_model_output(client, fake_completion):
    fake_completion.text = "   "
    response = client.post("/api/v1/estimate", json={"transcription": "Reunión X"})
    assert response.status_code == 502


def test_surrounding_whitespace_does_not_break_cache(client, fake_completion):
    first = client.post("/api/v1/estimate", json={"transcription": "Reunión X"})
    second = client.post("/api/v1/estimate", json={"transcription": "\n  Reunión X \n"})
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert fake_completion.calls == ["anthropic"]


def test_whitespace_only_transcription_is_rejected(client):
    response = client.post("/api/v1/estimate", json={"transcription": "  \n "})
    assert response.status_code == 422


def test_transcription_length_is_limited(client):
    too_long = "a" * (settings.max_transcription_chars + 1)
    response = client.post("/api/v1/estimate", json={"transcription": too_long})
    assert response.status_code == 422


def test_stream_emits_deltas_then_done(client, fake_completion):
    fake_completion.failing = {"anthropic"}
    response = client.post("/api/v1/estimate/stream", json={"transcription": "Reunión X"})
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "".join(d["delta"] for e, d in events if e == "delta") == "Estimación: 40 horas"
    done = events[-1]
    assert done[0] == "done"
    assert done[1]["provider"] == "openai"


def test_stream_emits_error_event_when_interrupted(client, fake_completion):
    fake_completion.fail_mid_stream = {"anthropic"}
    response = client.post("/api/v1/estimate/stream", json={"transcription": "Reunión X"})
    events = _sse_events(response.text)
    assert events[-1][0] == "error"
    assert "conexión perdida" not in response.text


def test_request_id_is_generated_or_propagated(client):
    assert client.get("/health").headers["x-request-id"]
    propagated = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert propagated.headers["x-request-id"] == "abc-123"
    sanitized = client.get("/health", headers={"X-Request-ID": "bad id <script>"})
    assert sanitized.headers["x-request-id"] != "bad id <script>"
