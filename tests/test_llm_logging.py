"""Qué registra el wrapper en cada llamada al LLM (inicio, cierre y errores)."""

import pytest

from app.services.llm_gateway import classify_error, litellm
from tests.conftest import make_gateway

E = litellm.exceptions


def _events(logs, name):
    return [fields for _, event, fields in logs if event == name]


def test_start_logs_model_provider_estimated_tokens_and_cache(gateway, fake_completion, logs):
    gateway.complete("system", "transcripción")
    gateway.complete("system", "transcripción")

    first, second = _events(logs, "llm.call_started")
    assert first["requested_model"] == "anthropic/claude-haiku-4-5"
    assert first["target_provider"] == "anthropic"
    assert first["input_tokens_estimated"] > 0
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True


def test_completion_logs_tokens_latency_cost_and_finish_reason(gateway, fake_completion, logs):
    gateway.complete("system", "transcripción")

    (done,) = _events(logs, "llm.call_completed")
    assert done["provider"] == "anthropic"
    assert done["input_tokens"] > 0 and done["output_tokens"] > 0
    assert done["latency_ms"] >= 0
    assert done["cost_usd"] > 0
    assert done["finish_reason"] == "stop"
    assert done["fallback_used"] is False
    assert done["fallback_provider"] is None
    assert done["attempts"] == 1


def test_cache_hit_logs_zero_cost(gateway, fake_completion, logs):
    gateway.complete("system", "transcripción")
    gateway.complete("system", "transcripción")

    _, hit = _events(logs, "llm.call_completed")
    assert hit["cache_hit"] is True
    assert hit["cost_usd"] == 0.0


def test_fallback_logs_target_provider(gateway, fake_completion, logs):
    fake_completion.failing = {"anthropic"}
    gateway.complete("system", "transcripción")

    (fallback,) = _events(logs, "llm.fallback")
    assert fallback == {
        "from_provider": "anthropic",
        "to_provider": "openai",
        "to_model": "openai/gpt-4o-mini",
    }
    (done,) = _events(logs, "llm.call_completed")
    assert done["fallback_used"] is True
    assert done["fallback_provider"] == "openai"


def test_transient_errors_are_retried_and_numbered(fake_completion, logs):
    gateway = make_gateway(max_retries=2)
    fake_completion.failing = {"anthropic"}
    fake_completion.error_cls = E.RateLimitError
    gateway.complete("system", "transcripción")

    failures = _events(logs, "llm.provider_failed")
    assert [(f["retry"], f["will_retry"], f["will_fallback"]) for f in failures] == [
        (0, True, False),
        (1, True, False),
        (2, False, True),
    ]
    assert all(f["error_category"] == "rate_limit" for f in failures)
    assert all(f["provider"] == "anthropic" for f in failures)
    assert fake_completion.calls == ["anthropic"] * 3 + ["openai"]
    assert _events(logs, "llm.call_completed")[0]["attempts"] == 4


def test_auth_errors_are_not_retried(fake_completion, logs):
    gateway = make_gateway(max_retries=2)
    fake_completion.failing = {"anthropic"}
    fake_completion.error_cls = E.AuthenticationError
    gateway.complete("system", "transcripción")

    (failure,) = _events(logs, "llm.provider_failed")
    assert failure["error_category"] == "auth"
    assert failure["will_retry"] is False
    assert failure["will_fallback"] is True


def test_last_provider_failure_reports_no_fallback(gateway, fake_completion, logs):
    fake_completion.failing = {"anthropic", "openai"}
    with pytest.raises(Exception):
        gateway.complete("system", "transcripción")

    *_, last = _events(logs, "llm.provider_failed")
    assert last["provider"] == "openai"
    assert last["will_fallback"] is False
    (failed,) = _events(logs, "llm.call_failed")
    assert failed["attempts"] == 2


def test_stream_logs_completion_with_cost_and_finish_reason(gateway, fake_completion, logs):
    "".join(gateway.stream("system", "transcripción"))

    (done,) = _events(logs, "llm.call_completed")
    assert done["finish_reason"] == "stop"
    assert done["cost_usd"] > 0
    assert done["output_tokens"] > 0


def test_logs_never_contain_the_transcription(gateway, fake_completion, logs):
    gateway.complete("system", "SECRETO-DEL-CLIENTE")
    assert "SECRETO-DEL-CLIENTE" not in repr(logs)


@pytest.mark.parametrize(
    ("exc_cls", "category"),
    [
        (E.Timeout, "timeout"),
        (E.RateLimitError, "rate_limit"),
        (E.AuthenticationError, "auth"),
        (E.InternalServerError, "server_error"),
        (E.ServiceUnavailableError, "server_error"),
        (E.APIConnectionError, "connection"),
        (E.BadRequestError, "bad_request"),
        (E.ContextWindowExceededError, "bad_request"),
    ],
)
def test_error_classification(exc_cls, category):
    exc = exc_cls(message="x", llm_provider="openai", model="gpt-4o-mini")
    assert classify_error(exc) == category
