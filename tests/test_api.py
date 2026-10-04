import logging

import pytest
from fastapi.testclient import TestClient
from instructor.core.exceptions import InstructorRetryException

from app.config import get_settings
from app.embeddings import get_embedder
from app.guardrails.moderation import get_moderation_client
from app.main import app
from app.schemas import OUT_OF_SCOPE_PREFIX, EstimationResponse, EstimationResult
from app.semantic_cache.factory import get_semantic_cache
from app.services.llm_gateway import get_llm_gateway, litellm
from tests.conftest import VALID_RESULT, guardrail_events, make_gateway, make_settings

VALID_REQUEST = {
    "description": "Portal interno para reservar salas y puestos de trabajo.",
    "project_type": "internal_tool",
    "detail_level": "medium",
    "output_format": "phases_table",
}

# Totales que no cuadran con las fases (la suma de costes es 6600).
INCONSISTENT_RESULT = VALID_RESULT | {"total_cost_eur": 9000}

OUT_OF_SCOPE_RESULT = {
    "summary": f"{OUT_OF_SCOPE_PREFIX} la descripción no es un proyecto de software.",
    "total_hours": 0,
    "total_duration_weeks": 0,
    "total_cost_eur": 0,
    "confidence_pct": 0,
    "phases": [],
}

CHATBOT_REQUEST = VALID_REQUEST | {
    "description": (
        "Chatbot de soporte para nuestra web: hay que diseñar el system prompt, "
        "conectarlo a la base de conocimiento y medir la calidad de las respuestas."
    )
}


@pytest.fixture
def api(gateway, fake_completion, fake_moderation, fake_embedder, fake_cache):
    """Cliente HTTP con LLM y moderación falsos y settings herméticos.
    `api(**settings)` devuelve un TestClient con esos settings."""

    def build(gateway_override=None, **settings_overrides) -> TestClient:
        settings = make_settings(**settings_overrides)
        # Sin `with`: no se ejecuta el lifespan, así que no hacen falta claves reales.
        app.dependency_overrides[get_llm_gateway] = lambda: gateway_override or gateway
        app.dependency_overrides[get_moderation_client] = lambda: fake_moderation
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_embedder] = lambda: fake_embedder
        app.dependency_overrides[get_semantic_cache] = lambda: fake_cache
        return TestClient(app, raise_server_exceptions=False)

    yield build
    app.dependency_overrides.clear()


@pytest.fixture
def client(api) -> TestClient:
    return api()


def _assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    return body


# --------------------------------------------------------- respuesta válida


def test_estimate_returns_the_structured_result_and_prompt_version(client):
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 200
    assert response.json() == {
        "result": VALID_RESULT,
        "prompt_version": "v3",
        "cached": False,
        "out_of_scope": False,
    }
    EstimationResponse.model_validate(response.json())


def test_out_of_scope_result_is_200_with_flag(client, fake_completion):
    fake_completion.structured = [OUT_OF_SCOPE_RESULT]
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 200
    assert response.json() == {
        "result": OUT_OF_SCOPE_RESULT,
        "prompt_version": "v3",
        "cached": False,
        "out_of_scope": True,
    }


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
    assert "<scope>" in system["content"] and OUT_OF_SCOPE_PREFIX in system["content"]


def test_form_options_change_the_prompt(client, fake_completion):
    client.post("/api/v1/estimate", json=VALID_REQUEST)
    table_system = fake_completion.last_messages[0]["content"]

    client.post("/api/v1/estimate", json=VALID_REQUEST | {"output_format": "narrative"})
    narrative_system = fake_completion.last_messages[0]["content"]

    assert table_system != narrative_system
    # Opciones distintas -> prompt distinto -> no comparten entrada de caché.
    assert fake_completion.calls == ["anthropic", "anthropic"]


# --------------------------------------------- FIX_RETRY (validadores, 502)


def test_validation_errors_are_sent_back_to_the_model_and_retried(client, fake_completion):
    fake_completion.structured = [INCONSISTENT_RESULT, VALID_RESULT]
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["result"] == VALID_RESULT
    assert fake_completion.calls == ["anthropic", "anthropic"]
    retry_messages = repr(fake_completion.last_messages)
    assert "total_cost_eur (9000) does not match" in retry_messages


def test_low_confidence_in_scope_result_is_retried_as_out_of_scope(api, fake_completion):
    client = api(min_confidence_pct=50)
    low_confidence = VALID_RESULT | {"confidence_pct": 40}
    fake_completion.structured = [low_confidence, OUT_OF_SCOPE_RESULT]

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["out_of_scope"] is True
    # El umbral configurado llega al validador vía Instructor (contexto).
    assert "Confidence below 50%" in repr(fake_completion.last_messages)


def test_invalid_output_after_all_retries_returns_502(client, fake_completion):
    fake_completion.structured = [INCONSISTENT_RESULT]
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    _assert_error(response, 502, "estimation_failed")
    # 1 intento + 2 reintentos de validación, sin rotar de proveedor: el
    # proveedor funciona, es la respuesta la que no cumple el contrato.
    assert fake_completion.calls == ["anthropic"] * 3
    assert "9000" not in response.text


def test_out_of_scope_with_values_is_rejected_by_the_contract(client, fake_completion):
    fake_completion.structured = [OUT_OF_SCOPE_RESULT | {"total_hours": 10}]
    _assert_error(client.post("/api/v1/estimate", json=VALID_REQUEST), 502, "estimation_failed")


def test_malformed_json_from_the_model_returns_502(client, fake_completion):
    fake_completion.structured = ['{"summary": "sin cerrar"']
    _assert_error(client.post("/api/v1/estimate", json=VALID_REQUEST), 502, "estimation_failed")


# ------------------------------------------------------------- capa 1 (422)


@pytest.mark.parametrize(
    "override",
    [
        {"description": "Muy corta"},  # < 20 caracteres
        {"description": "a" * 10_001},  # > techo absoluto del schema
        {"description": "   " + "x" * 5 + "   "},  # < 20 tras recortar espacios
        {"project_type": "videojuego"},
        {"detail_level": "extremo"},
        {"output_format": "pdf"},
        {"campo_desconocido": "x"},  # extra="forbid"
    ],
)
def test_invalid_requests_are_rejected(client, override, fake_moderation):
    response = client.post("/api/v1/estimate", json=VALID_REQUEST | override)
    assert response.status_code == 422
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert fake_moderation.calls == []


def test_configured_description_max_length_is_enforced_without_echo(api, fake_moderation):
    client = api(description_max_length=60)
    description = "Descripción secreta del proyecto " + "x" * 40
    response = client.post("/api/v1/estimate", json=VALID_REQUEST | {"description": description})

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["type"] == "string_too_long" and error["loc"] == ["body", "description"]
    assert "secreta" not in response.text
    assert fake_moderation.calls == []


def test_validation_errors_never_echo_the_input(client):
    response = client.post("/api/v1/estimate", json=VALID_REQUEST | {"description": "Mi secreto"})
    assert response.status_code == 422
    assert "Mi secreto" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


# ------------------------------------------------- capa 2: moderación (400/503)


def test_flagged_by_moderation_returns_generic_400(client, fake_moderation, fake_completion):
    fake_moderation.flagged = True
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    body = _assert_error(response, 400, "input_rejected")
    assert "violence" not in response.text and "moderation" not in response.text
    assert VALID_REQUEST["description"] not in response.text
    assert body["error"]["message"] == "No se ha podido procesar la descripción."
    assert fake_completion.calls == []


def test_not_flagged_continues(client, fake_moderation):
    assert client.post("/api/v1/estimate", json=VALID_REQUEST).status_code == 200
    assert fake_moderation.calls == [VALID_REQUEST["description"]]


def test_moderation_timeout_fail_closed_returns_503(client, fake_moderation, fake_completion):
    fake_moderation.unavailable = True
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    _assert_error(response, 503, "moderation_unavailable")
    assert response.headers["retry-after"] == "30"
    assert fake_completion.calls == []


def test_moderation_timeout_fail_open_continues_and_logs(api, fake_moderation, caplog):
    client = api(moderation_fail_closed=False)
    fake_moderation.unavailable = True
    with caplog.at_level(logging.INFO):
        response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 200
    (event,) = guardrail_events(caplog, "guardrail.unavailable")
    assert event["guardrail"] == "moderation"
    assert event["fail_closed"] is False
    assert event["request_id"] == response.headers["x-request-id"]


# ---------------------------------------- capa 2: injection y PII (LOG_ONLY)


def test_injection_false_positive_is_logged_but_not_blocked_in_log_only(client, caplog):
    with caplog.at_level(logging.INFO):
        response = client.post("/api/v1/estimate", json=CHATBOT_REQUEST)

    # LOG_ONLY: la respuesta es la misma que sin disparo.
    assert response.status_code == 200
    assert response.json()["result"] == VALID_RESULT
    (event,) = [e for e in guardrail_events(caplog) if e["guardrail"] == "prompt_injection"]
    assert event["triggered"] is True
    assert event["mode"] == "log_only"
    assert event["policy"] == "exception"
    assert event["detail"] == "system_prompt"
    assert event["request_id"] == response.headers["x-request-id"]


def test_injection_in_enforce_blocks_without_revealing_the_pattern(api, fake_moderation):
    client = api(guardrail_injection_mode="enforce")
    response = client.post("/api/v1/estimate", json=CHATBOT_REQUEST)

    _assert_error(response, 400, "input_rejected")
    assert "system prompt" not in response.text and "system_prompt" not in response.text
    assert CHATBOT_REQUEST["description"] not in response.text
    # Las heurísticas locales van antes que la moderación (no se gasta red).
    assert fake_moderation.calls == []


def test_pii_input_in_enforce_blocks(api):
    client = api(guardrail_pii_input_mode="enforce")
    request = VALID_REQUEST | {"description": "Portal de reservas; contacto: ana.lopez@empresa.es"}
    response = client.post("/api/v1/estimate", json=request)
    _assert_error(response, 400, "input_rejected")
    assert "ana.lopez" not in response.text


def test_logs_never_contain_the_description_nor_the_pii(client, caplog):
    email, iban = "ana.lopez@empresa.es", "ES91 2100 0418 4502 0005 1332"
    description = f"Portal de reservas de salas. Contacto {email}, cobro en {iban}."
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/v1/estimate", json=VALID_REQUEST | {"description": description})

    assert response.status_code == 200
    (pii,) = [e for e in guardrail_events(caplog) if e["guardrail"] == "pii_input"]
    assert pii["triggered"] is True and pii["detail"] == "email,iban"
    assert all(e.get("description_sha256") for e in guardrail_events(caplog))
    assert description not in caplog.text
    assert email not in caplog.text
    assert iban not in caplog.text and iban.replace(" ", "") not in caplog.text


# ------------------------------------------------- capa 5: PII en el output


def _result_with_pii():
    phases = [dict(VALID_RESULT["phases"][0], assumptions=["el PM es ana.lopez@empresa.es"]), VALID_RESULT["phases"][1]]
    return VALID_RESULT | {"summary": "Llamar al 612 345 678 para dudas.", "phases": phases}


def test_output_pii_is_logged_but_kept_in_log_only(client, fake_completion):
    fake_completion.structured = [_result_with_pii()]
    result = client.post("/api/v1/estimate", json=VALID_REQUEST).json()["result"]
    assert result["summary"] == "Llamar al 612 345 678 para dudas."


def test_output_pii_is_redacted_in_enforce(api, fake_completion):
    client = api(guardrail_pii_output_mode="enforce")
    fake_completion.structured = [_result_with_pii()]
    result = client.post("/api/v1/estimate", json=VALID_REQUEST).json()["result"]

    assert result["summary"] == "Llamar al [REDACTED] para dudas."
    assert result["phases"][0]["assumptions"] == ["el PM es [REDACTED]"]
    assert result["total_hours"] == VALID_RESULT["total_hours"]


# ------------------------------------------------ proveedores y caché (503)


def test_estimate_returns_503_without_internal_details(client, fake_completion):
    fake_completion.failing = {"anthropic", "openai"}
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    _assert_error(response, 503, "llm_unavailable")
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


def test_openapi_documents_the_contract_and_error_responses(client):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    response = schemas["EstimationResponse"]
    assert response["properties"]["result"]["$ref"].endswith("/EstimationResult")
    assert response["properties"]["out_of_scope"]["type"] == "boolean"
    assert {"Phase", "EstimationResult", "ErrorResponse"} <= schemas.keys()

    responses = spec["paths"]["/api/v1/estimate"]["post"]["responses"]
    for status in ("400", "502", "503"):
        (content,) = responses[status]["content"].values()
        assert content["schema"]["$ref"].endswith("/ErrorResponse")


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


def test_endpoint_with_mocked_instructor_returns_estimation_response(api):
    fixed = EstimationResult.model_validate(VALID_RESULT)
    fake = FakeInstructor(fixed)
    client = api(gateway_override=make_gateway(structured_client=fake))

    response = client.post("/api/v1/estimate?prompt_version=v2", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json() == EstimationResponse(result=fixed, prompt_version="v2").model_dump(
        mode="json"
    )
    (call,) = fake.calls
    assert call["response_model"] is EstimationResult
    assert call["max_retries"] == 2
    assert call["context"] == {"min_confidence_pct": 30}


def test_endpoint_with_mocked_instructor_retries_exhausted_returns_502(api, logs):
    fake = FakeInstructor(InstructorRetryException("validation failed", n_attempts=3, total_usage=0))
    client = api(gateway_override=make_gateway(structured_client=fake))

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    _assert_error(response, 502, "estimation_failed")
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
    v3_system = fake_completion.last_messages[0]["content"]

    assert "fisioterapia" in v2_system and "fisioterapia" not in v3_system
    # Versión distinta -> prompt distinto -> no comparten entrada de caché.
    assert fake_completion.calls == ["anthropic", "anthropic"]


@pytest.mark.parametrize("version", ["v99", "latest", "../v1", ""])
def test_unknown_prompt_version_is_rejected_without_calling_anything(
    client, fake_completion, fake_moderation, version
):
    response = client.post(f"/api/v1/estimate?prompt_version={version}", json=VALID_REQUEST)
    assert response.status_code == 422
    assert fake_completion.calls == []
    assert fake_moderation.calls == []


def test_request_id_is_generated_or_propagated(client):
    assert client.get("/health").headers["x-request-id"]
    propagated = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert propagated.headers["x-request-id"] == "abc-123"
    sanitized = client.get("/health", headers={"X-Request-ID": "bad id <script>"})
    assert sanitized.headers["x-request-id"] != "bad id <script>"


# ------------------------------------------------------------ caché semántico


def test_active_semantic_cache_serves_equivalent_request_without_llm(api, fake_completion, fake_moderation):
    client = api(semantic_cache_mode="active")
    first = client.post("/api/v1/estimate", json=VALID_REQUEST)
    second = client.post("/api/v1/estimate", json=VALID_REQUEST | {"description": "  " + VALID_REQUEST["description"]})

    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json() == first.json() | {"cached": True}
    assert fake_completion.calls == ["anthropic"]
    # Un hit nunca se salta la moderación de entrada.
    assert len(fake_moderation.calls) == 2


def test_rejected_input_never_reaches_the_semantic_cache(api, fake_moderation, fake_embedder, fake_cache):
    client = api(semantic_cache_mode="active")
    fake_moderation.flagged = True
    assert client.post("/api/v1/estimate", json=VALID_REQUEST).status_code == 400
    assert fake_embedder.calls == [] and fake_cache.lookups == []


def test_semantic_cache_failure_does_not_break_the_endpoint(api, fake_cache):
    client = api(semantic_cache_mode="active")
    fake_cache.failing = True
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)
    assert response.status_code == 200 and response.json()["cached"] is False


def test_openapi_documents_the_cached_flag(client):
    props = client.get("/openapi.json").json()["components"]["schemas"]["EstimationResponse"]["properties"]
    assert props["cached"]["type"] == "boolean"
    assert "caché semántico" in props["cached"]["description"]
