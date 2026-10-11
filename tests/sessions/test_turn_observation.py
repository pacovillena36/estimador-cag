"""Evento `turn_observed`: exactamente uno por turno, con los 13 campos del
contrato (TurnObservation) y sin contenido de la conversación. Por HTTP con
el LLM falso de test_session_turns."""

import dataclasses
import logging
import typing

import pytest

from app.guardrails.moderation import get_moderation_client
from app.observability.turn_observation import TURN_OBSERVED_EVENT, TurnObservation
from app.services.llm_gateway import get_llm_gateway
from tests.conftest import FakeModeration, guardrail_events
from tests.sessions.conftest import create_session
from tests.sessions.files import make_pdf
from tests.sessions.test_session_turns import FakeLLM, state, turn

pytestmark = pytest.mark.anyio

FIELDS = {f.name: f.type for f in dataclasses.fields(TurnObservation)}
SECRET = "Proyecto Zafiro-Q9 con presupuesto de 30000 EUR."


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def overrides(overrides, fake_llm) -> dict:
    return overrides | {get_llm_gateway: lambda: fake_llm, get_moderation_client: lambda: FakeModeration()}


def observed(caplog) -> list[dict]:
    return guardrail_events(caplog, TURN_OBSERVED_EVENT)


def _check_types(event: dict) -> None:
    for name, annotation in FIELDS.items():
        value = event[name]
        if typing.get_origin(annotation) is typing.Literal:
            assert value in typing.get_args(annotation), name
        elif annotation is float:
            assert isinstance(value, float), name
        else:
            assert type(value) is annotation, name  # bool no cuela como int


async def test_one_event_per_turn_with_the_13_fields(client, caplog):
    caplog.set_level(logging.INFO)
    session_id = await create_session(client)

    for index in (1, 2):
        caplog.clear()
        response = await turn(client, session_id, f"Turno {index} del portal de reservas.")
        assert response.status_code == 200
        events = observed(caplog)
        assert len(events) == 1
        event = events[0]
        assert len(FIELDS) == 13 and FIELDS.keys() <= event.keys()
        _check_types(event)
        assert event["turn_index"] == index
        assert event["session_id"] == session_id[:8]
        assert event["messages_in_window"] == 2 * index
        assert event["attachments_total_chars"] == 0
        assert event["cache_hit_kind"] == "none"
        assert event["last_resolved_tier"] == "fast"
        assert event["tokens_in"] > 0 and event["cost_usd"] > 0 and event["latency_ms"] >= 0


async def test_attachment_chars_are_counted(client, caplog):
    caplog.set_level(logging.INFO)
    session_id = await create_session(client)
    files = [("attachments", ("requisitos.pdf", make_pdf("Requisitos del portal"), "application/pdf"))]
    await turn(client, session_id, "Con adjunto.", files=files)
    event = observed(caplog)[0]
    assert event["attachments_total_chars"] > 0
    assert event["enriched_transcript_chars"] > event["attachments_total_chars"]


async def test_failed_turn_emits_no_event(client, caplog, fake_llm):
    caplog.set_level(logging.INFO)
    session_id = await create_session(client)
    fake_llm.invalid_estimation = True
    response = await turn(client, session_id, "Turno que falla.")
    assert response.status_code == 502
    assert observed(caplog) == []


async def test_cached_actor_call_is_an_exact_hit(client, caplog, fake_llm, monkeypatch):
    caplog.set_level(logging.INFO)
    original = fake_llm.complete_structured_with_usage

    def cached(messages, response_model, **kwargs):
        completion = original(messages, response_model, **kwargs)
        hit = dataclasses.replace(completion.usage, cached=True, cost_usd=0.0, tokens_in=0, tokens_out=0)
        return dataclasses.replace(completion, usage=hit)

    monkeypatch.setattr(fake_llm, "complete_structured_with_usage", cached)
    session_id = await create_session(client)
    await turn(client, session_id, "Turno servido desde la caché.")
    event = observed(caplog)[0]
    assert event["cache_hit_kind"] == "exact"
    assert (event["cost_usd"], event["tokens_in"], event["tokens_out"]) == (0.0, 0, 0)


async def test_event_never_carries_conversation_text(client, caplog):
    caplog.set_level(logging.INFO)
    session_id = await create_session(client)
    await turn(client, session_id, SECRET)
    assert all(not isinstance(v, str) or "Zafiro" not in v for v in observed(caplog)[0].values())


async def test_state_hides_observation_and_memory_by_default(client):
    session_id = await create_session(client)
    await turn(client, session_id, SECRET)
    info = await state(client, session_id)
    assert "last_turn_observation" not in info and "memory_debug" not in info


@pytest.mark.parametrize(
    "session_settings",
    [{"expose_turn_observation": True, "expose_session_memory_debug": True, "max_conversation_turns": 1}],
)
async def test_state_exposes_observation_and_memory_behind_flags(client):
    session_id = await create_session(client)
    await turn(client, session_id, SECRET)
    await turn(client, session_id, "Segundo turno.")  # el primero sale de la ventana
    info = await state(client, session_id)
    observation = info["last_turn_observation"]
    assert set(observation) == set(FIELDS) and observation["turn_index"] == 2
    assert any("30000" in anchor for anchor in info["memory_debug"]["anchors"])
    assert info["memory_debug"]["summary"] != ""
