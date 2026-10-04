import pytest
from pydantic import SecretStr

from app.config import Settings
from app.schemas import EstimationResult
from app.services.cache import TTLCache
from app.services.llm_gateway import (
    AllProvidersFailedError,
    InvalidStructuredOutputError,
    LLMGateway,
    NoProvidersConfiguredError,
    ProviderError,
    build_providers,
)
from tests.conftest import VALID_RESULT


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "openai_api_key": "sk-o",
        "anthropic_api_key": "sk-a",
        "llm_provider": "anthropic",
    }
    return Settings(**(base | overrides))


# --------------------------------------------------------- abstracción/orden


def test_preferred_provider_goes_first_then_fallback():
    providers = build_providers(_settings(llm_provider="openai"))
    assert [p.name for p in providers] == ["openai", "anthropic"]


def test_providers_without_api_key_are_skipped():
    providers = build_providers(_settings(openai_api_key=None))
    assert [p.name for p in providers] == ["anthropic"]


def test_fallback_disabled_keeps_only_preferred_provider():
    providers = build_providers(_settings(llm_fallback_enabled=False))
    assert [p.name for p in providers] == ["anthropic"]


def test_gateway_requires_at_least_one_provider():
    with pytest.raises(NoProvidersConfiguredError):
        LLMGateway([], timeout_seconds=1, max_retries=0, max_tokens=1)


def test_api_keys_never_appear_in_repr():
    provider = build_providers(_settings(anthropic_api_key=SecretStr("sk-secreta")))[0]
    assert "sk-secreta" not in repr(provider)
    assert "sk-secreta" not in repr(_settings(anthropic_api_key="sk-secreta"))


# ------------------------------------------------------------------ fallback


def test_complete_uses_primary_when_it_works(gateway, fake_completion):
    result = gateway.complete("system", "transcripción")
    assert result.provider == "anthropic"
    assert result.text == "Estimación: 40 horas"
    assert result.input_tokens and result.output_tokens
    assert fake_completion.calls == ["anthropic"]


def test_complete_rotates_to_next_provider_on_failure(gateway, fake_completion):
    fake_completion.failing = {"anthropic"}
    result = gateway.complete("system", "transcripción")
    assert result.provider == "openai"
    assert fake_completion.calls == ["anthropic", "openai"]


def test_complete_raises_when_all_providers_fail(gateway, fake_completion):
    fake_completion.failing = {"anthropic", "openai"}
    with pytest.raises(AllProvidersFailedError):
        gateway.complete("system", "transcripción")


def test_stream_rotates_before_first_chunk(gateway, fake_completion):
    fake_completion.failing = {"anthropic"}
    stream = gateway.stream("system", "transcripción")
    assert "".join(stream) == "Estimación: 40 horas"
    assert stream.response.provider == "openai"
    assert stream.response.output_tokens is not None


def test_stream_raises_all_failed_before_returning(gateway, fake_completion):
    fake_completion.failing = {"anthropic", "openai"}
    with pytest.raises(AllProvidersFailedError):
        gateway.stream("system", "transcripción")


def test_stream_failure_after_first_chunk_is_not_rotated_nor_cached(gateway, fake_completion):
    fake_completion.fail_mid_stream = {"anthropic"}
    stream = gateway.stream("system", "transcripción")
    with pytest.raises(ProviderError):
        list(stream)
    assert fake_completion.calls == ["anthropic"]

    fake_completion.fail_mid_stream = set()
    assert gateway.complete("system", "transcripción").cached is False


# --------------------------------------------------------------------- caché


def test_identical_request_is_served_from_cache(gateway, fake_completion):
    first = gateway.complete("system", "transcripción")
    second = gateway.complete("system", "transcripción")
    assert first.cached is False
    assert second.cached is True
    assert second.text == first.text
    assert fake_completion.calls == ["anthropic"]


def test_cache_is_exact_match(gateway, fake_completion):
    gateway.complete("system", "transcripción")
    assert gateway.complete("system", "transcripción ").cached is False
    assert gateway.complete("otro system", "transcripción").cached is False


def test_stream_result_is_cached_and_replayed(gateway, fake_completion):
    "".join(gateway.stream("system", "transcripción"))
    replay = gateway.stream("system", "transcripción")
    assert "".join(replay) == "Estimación: 40 horas"
    assert replay.response.cached is True
    assert fake_completion.calls == ["anthropic"]


def test_cache_evicts_least_recently_used_and_expires():
    cache = TTLCache[str](ttl_seconds=60, max_entries=2)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.get("a")
    cache.set("c", "3")
    assert cache.get("b") is None and cache.get("a") == "1"

    expired = TTLCache[str](ttl_seconds=0, max_entries=2)
    expired.set("a", "1")
    assert expired.get("a") is None


# ------------------------------------------------------ salida estructurada


def test_complete_structured_returns_a_validated_instance(gateway, fake_completion):
    result = gateway.complete_structured("system", "transcripción", EstimationResult)
    assert isinstance(result, EstimationResult)
    assert result.model_dump() == VALID_RESULT


def test_complete_structured_is_cached_separately_from_text(gateway, fake_completion):
    gateway.complete("system", "transcripción")
    first = gateway.complete_structured("system", "transcripción", EstimationResult)
    second = gateway.complete_structured("system", "transcripción", EstimationResult)
    assert second == first
    # Una llamada de texto y una estructurada; la segunda estructurada, de caché.
    assert fake_completion.calls == ["anthropic", "anthropic"]


def test_complete_structured_rotates_provider_on_transport_errors(gateway, fake_completion):
    fake_completion.failing = {"anthropic"}
    gateway.complete_structured("system", "transcripción", EstimationResult)
    assert fake_completion.calls == ["anthropic", "openai"]


def test_invalid_structured_output_is_not_cached_nor_rotated(gateway, fake_completion, logs):
    fake_completion.structured = [VALID_RESULT | {"total_hours": 1}]
    with pytest.raises(InvalidStructuredOutputError):
        gateway.complete_structured("system", "transcripción", EstimationResult)
    assert fake_completion.calls == ["anthropic"] * 3

    (failed,) = [fields for _, event, fields in logs if event == "llm.validation_failed"]
    assert failed["errors"] == ["<root>: value_error"]
    assert "total_hours" not in repr(failed)  # ni valores ni mensajes del modelo

    fake_completion.structured = [VALID_RESULT]
    gateway.complete_structured("system", "transcripción", EstimationResult)
    assert fake_completion.calls == ["anthropic"] * 4


def test_validation_context_reaches_validators_and_is_part_of_the_cache_key(gateway, fake_completion):
    low = VALID_RESULT | {"confidence_pct": 40}
    fake_completion.structured = [low]

    lenient = gateway.complete_structured(
        "system", "transcripción", EstimationResult, context={"min_confidence_pct": 30}
    )
    assert lenient.confidence_pct == 40
    with pytest.raises(InvalidStructuredOutputError):
        gateway.complete_structured(
            "system", "transcripción", EstimationResult, context={"min_confidence_pct": 50}
        )
    # Otro umbral -> otra entrada de caché (1 llamada + 3 intentos).
    assert fake_completion.calls == ["anthropic"] * 4
