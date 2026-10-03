import pytest
from fastapi.testclient import TestClient
from instructor.core.exceptions import InstructorRetryException

from app.main import app
from app.schemas import EstimationResponse, EstimationResult
from app.services.llm_gateway import get_llm_gateway, litellm
from tests.conftest import VALID_RESULT, make_gateway

VALID_REQUEST = {
    "description": "Portal interno para reservar salas y puestos de trabajo.",
    "project_type": "internal_tool",
    "detail_level": "medium",
    "output_format": "phases_table",
}

# Totales que no cuadran con las fases (la suma de costes es 6600).
INCONSISTENT_RESULT = VALID_RESULT | {"total_cost_eur": 9000}


def _client_for(gateway) -> TestClient:
    # Sin `with`: no se ejecuta el lifespan, así que no hacen falta claves reales.
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    return TestClient(app)


@pytest.fixture
def client(gateway, fake_completion):
    yield _client_for(gateway)
    app.dependency_overrides.clear()


# ----------------------------------------- Instructor real sobre LiteLLM falso


def test_estimate_returns_the_structured_result_and_prompt_version(client):
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 200
    assert response.json() == {"result": VALID_RESULT, "prompt_version": "v1"}
    EstimationResponse.model_validate(response.json())


def test_schema_is_sent_to_the_provider_as_a_forced_tool(client, fake_completion):
    client.post("/api/v1/estimate", json=VALID_REQUEST)

    kwargs = fake_completion.last_kwargs
    (tool,) = kwargs["tools"]
    assert tool["function"]["name"] == "EstimationResult"
    assert set(tool["function"]["parameters"]["required"]) >= {"phases", "total_cost_eur"}
    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "EstimationResult"}}


def test_model_receives_separate_system_and_user_messages(client, fake_completion):
    client.post("/api/v1/estimate", json=VALID_REQUEST)

    system, user = fake_completion.last_messages
    assert system["role"] == "system"
    assert user["role"] == "user"
    assert VALID_REQUEST["description"] in user["content"]
    assert VALID_REQUEST["description"] not in system["content"]


def test_form_options_change_the_prompt(client, fake_completion):
    client.post("/api/v1/estimate", json=VALID_REQUEST)
    table_system = fake_completion.last_messages[0]["content"]

    client.post("/api/v1/estimate", json=VALID_REQUEST | {"output_format": "narrative"})
    narrative_system = fake_completion.last_messages[0]["content"]

    assert table_system != narrative_system
    # Opciones distintas -> prompt distinto -> no comparten entrada de caché.
    assert fake_completion.calls == ["anthropic", "anthropic"]


def test_validation_errors_are_sent_back_to_the_model_and_retried(client, fake_completion):
    fake_completion.structured = [INCONSISTENT_RESULT, VALID_RESULT]
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["result"] == VALID_RESULT
    assert fake_completion.calls == ["anthropic", "anthropic"]
    retry_messages = repr(fake_completion.last_messages)
    assert "total_cost_eur (9000) does not match" in retry_messages


def test_invalid_output_after_all_retries_returns_502(client, fake_completion):
    fake_completion.structured = [INCONSISTENT_RESULT]
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "El modelo no devolvió una estimación válida. Inténtalo de nuevo."
    }
    # 1 intento + 2 reintentos de validación, sin rotar de proveedor: el
    # proveedor funciona, es la respuesta la que no cumple el contrato.
    assert fake_completion.calls == ["anthropic"] * 3


def test_malformed_json_from_the_model_returns_502(client, fake_completion):
    fake_completion.structured = ['{"summary": "sin cerrar"']
    assert client.post("/api/v1/estimate", json=VALID_REQUEST).status_code == 502


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


def test_provider_errors_still_fall_back_with_structured_output(client, fake_completion):
    fake_completion.failing = {"anthropic"}
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 200
    assert fake_completion.calls == ["anthropic", "openai"]


def test_surrounding_whitespace_does_not_break_cache(client, fake_completion):
    padded = VALID_REQUEST | {"description": "\n  " + VALID_REQUEST["description"] + " \n"}
    first = client.post("/api/v1/estimate", json=VALID_REQUEST)
    second = client.post("/api/v1/estimate", json=padded)
    assert second.json() == first.json()
    assert fake_completion.calls == ["anthropic"]


def test_stream_endpoint_no_longer_exists(client):
    response = client.post("/api/v1/estimate/stream", json=VALID_REQUEST)
    assert response.status_code in (404, 405)


def test_openapi_documents_the_structured_response(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert schemas["EstimationResponse"]["properties"]["result"]["$ref"].endswith(
        "/EstimationResult"
    )
    assert {"Phase", "EstimationResult"} <= schemas.keys()


# ------------------------------------------------- cliente Instructor mockeado


class FakeInstructor:
    """Sustituye al cliente de Instructor: devuelve un resultado fijo o
    lanza la excepción indicada, sin pasar por LiteLLM."""

    def __init__(self, outcome: EstimationResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    def create_with_completion(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome, litellm.ModelResponse(model="mock-model")


@pytest.fixture
def instructor_client():
    def build(outcome):
        fake = FakeInstructor(outcome)
        return _client_for(make_gateway(structured_client=fake)), fake

    yield build
    app.dependency_overrides.clear()


def test_endpoint_with_mocked_instructor_returns_estimation_response(instructor_client):
    fixed = EstimationResult.model_validate(VALID_RESULT)
    client, fake = instructor_client(fixed)

    response = client.post("/api/v1/estimate?prompt_version=v2", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json() == EstimationResponse(result=fixed, prompt_version="v2").model_dump(
        mode="json"
    )
    (call,) = fake.calls
    assert call["response_model"] is EstimationResult
    assert call["max_retries"] == 2


def test_endpoint_with_mocked_instructor_retries_exhausted_returns_502(instructor_client, logs):
    exhausted = InstructorRetryException("validation failed", n_attempts=3, total_usage=0)
    client, fake = instructor_client(exhausted)

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 502
    # Sin fallback al segundo proveedor: un solo intento de Instructor.
    assert len(fake.calls) == 1
    (failed,) = [fields for _, event, fields in logs if event == "llm.validation_failed"]
    assert failed["validation_attempts"] == 3
    assert failed["response_model"] == "EstimationResult"


# ----------------------------------------------------------- prompt_version


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


@pytest.mark.parametrize("version", ["v99", "latest", "../v1", ""])
def test_unknown_prompt_version_is_rejected_without_calling_the_model(
    client, fake_completion, version
):
    response = client.post(f"/api/v1/estimate?prompt_version={version}", json=VALID_REQUEST)
    assert response.status_code == 422
    assert fake_completion.calls == []


def test_request_id_is_generated_or_propagated(client):
    assert client.get("/health").headers["x-request-id"]
    propagated = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert propagated.headers["x-request-id"] == "abc-123"
    sanitized = client.get("/health", headers={"X-Request-ID": "bad id <script>"})
    assert sanitized.headers["x-request-id"] != "bad id <script>"
