"""Wrapper LLM: cadenas por tier, LLMUsage por llamada y tabla MODEL_COSTS."""

import json

from pydantic import SecretStr

from app.ports.llm import ModelTier
from app.schemas import EstimationResult
from app.services.llm_gateway import ProviderConfig, build_providers
from app.services.llm_pricing import MODEL_COSTS, cost_from_table
from tests.conftest import VALID_RESULT, make_gateway, make_settings

STRONG = ProviderConfig(
    name="anthropic",
    model="anthropic/claude-sonnet-5-5",
    api_key=SecretStr("sk-test-a"),
    structured_mode="md_json",
    supports_temperature=False,
)


def _messages() -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


# ----------------------------------------------------------------- precios


def test_cost_comes_from_the_table_with_or_without_provider_prefix():
    assert cost_from_table("anthropic/claude-haiku-4-5", 1_000_000, 1_000_000) == 6.0
    assert cost_from_table("claude-sonnet-5-5", 2_000, 1_000) == 0.014
    assert cost_from_table("modelo-desconocido", 10, 10) is None
    assert {"claude-haiku-4-5", "claude-sonnet-5-5", "gpt-4o-mini", "gpt-4o"} <= MODEL_COSTS.keys()


# ------------------------------------------------------------------- tiers


def test_build_providers_uses_strong_models_for_the_strong_tier():
    settings = make_settings(anthropic_api_key="sk-a", llm_provider="anthropic")
    fast = build_providers(settings)
    strong = build_providers(settings, ModelTier.STRONG)
    assert [p.model for p in fast] == [
        f"anthropic/{settings.anthropic_model}",
        f"openai/{settings.openai_model}",
    ]
    assert [p.model for p in strong] == ["anthropic/claude-sonnet-5-5", "openai/gpt-4o"]
    # Sonnet 5.5 rechaza tool_choice forzado y temperature: JSON en el texto.
    assert (strong[0].structured_mode, strong[0].supports_temperature) == ("md_json", False)
    assert (fast[0].structured_mode, fast[0].supports_temperature) == ("tools", True)


def test_structured_call_returns_parsed_instance_with_usage(fake_completion):
    completion = make_gateway().complete_structured_with_usage(_messages(), EstimationResult)
    assert isinstance(completion.parsed, EstimationResult)
    usage = completion.usage
    assert (usage.provider, usage.cached) == ("anthropic", False)
    assert usage.tokens_in and usage.tokens_out and usage.latency_ms >= 0
    assert usage.cost_usd == cost_from_table("claude-haiku-4-5", usage.tokens_in, usage.tokens_out)


def test_cached_call_reports_zero_cost(fake_completion):
    gateway = make_gateway()
    gateway.complete_structured_with_usage(_messages(), EstimationResult)
    again = gateway.complete_structured_with_usage(_messages(), EstimationResult)
    assert again.usage.cached and again.usage.cost_usd == 0.0


def test_strong_tier_uses_its_chain_md_json_and_drops_temperature(fake_completion):
    fake_completion.text = "```json\n" + json.dumps(VALID_RESULT) + "\n```"
    gateway = make_gateway(strong_providers=[STRONG])

    completion = gateway.complete_structured_with_usage(
        _messages(), EstimationResult, temperature=0, tier=ModelTier.STRONG
    )

    assert completion.parsed.total_hours == VALID_RESULT["total_hours"]
    assert fake_completion.last_kwargs["model"] == "anthropic/claude-sonnet-5-5"
    assert "tools" not in fake_completion.last_kwargs and "tool_choice" not in fake_completion.last_kwargs
    assert "temperature" not in fake_completion.last_kwargs
    assert completion.usage.model.endswith("claude-sonnet-5-5")


def test_fast_and_strong_answers_are_cached_separately(fake_completion):
    fake_completion.text = "```json\n" + json.dumps(VALID_RESULT) + "\n```"
    gateway = make_gateway(strong_providers=[STRONG])
    gateway.complete_structured_with_usage(_messages(), EstimationResult)
    strong = gateway.complete_structured_with_usage(_messages(), EstimationResult, tier=ModelTier.STRONG)
    assert not strong.usage.cached


def test_strong_tier_falls_back_to_the_fast_chain_when_not_configured(fake_completion):
    completion = make_gateway().complete_structured_with_usage(_messages(), EstimationResult, tier=ModelTier.STRONG)
    assert completion.usage.provider == "anthropic"
    assert fake_completion.last_kwargs["model"] == "anthropic/claude-haiku-4-5"


def test_response_exposes_observability_fields(fake_completion):
    response = make_gateway().complete("s", "u")
    assert response.latency_ms is not None and response.tokens_in == response.input_tokens
    assert response.usage.tokens_out == response.output_tokens
