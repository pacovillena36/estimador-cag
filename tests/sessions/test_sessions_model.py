"""Fase 1: ConversationHistory, ProjectMetadata y SessionStore (sin app)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.sessions import (
    ConversationHistory,
    ProjectMetadata,
    SessionCapacityError,
    SessionStore,
)


def test_history_puts_system_first_then_pairs_in_order():
    history = ConversationHistory(max_turns=6)
    history.add_turn("u1", "a1")
    history.add_turn("u2", "a2")

    messages = history.to_messages_list("SYSTEM")

    assert messages == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_history_window_drops_the_oldest_pairs_but_never_the_system():
    history = ConversationHistory(max_turns=3)
    for i in range(1, 6):
        history.add_turn(f"u{i}", f"a{i}")

    messages = history.to_messages_list("SYSTEM")

    assert len(history) == 3
    assert messages[0] == {"role": "system", "content": "SYSTEM"}
    assert [m["content"] for m in messages[1:]] == ["u3", "a3", "u4", "a4", "u5", "a5"]


def test_system_prompt_is_not_stored_in_history():
    history = ConversationHistory(max_turns=2)
    history.add_turn("u1", "a1")
    assert history.to_messages_list("V1")[0]["content"] == "V1"
    assert history.to_messages_list("V2")[0]["content"] == "V2"
    assert len(history) == 1


def test_metadata_is_empty_by_default_and_rejects_extra_fields():
    assert ProjectMetadata().is_empty()
    assert not ProjectMetadata(mentioned_technologies=["Python"]).is_empty()
    with pytest.raises(ValidationError):
        ProjectMetadata.model_validate({"project_name": "X", "budget": 1000})


@pytest.mark.parametrize(
    "data",
    [
        {"assumed_team_size": 0},
        {"assumed_team_size": 501},
        {"agreed_scope": "x" * 2001},
        {"mentioned_technologies": ["x" * 61]},
        {"mentioned_technologies": [f"t{i}" for i in range(31)]},
        {"mentioned_technologies": [""]},
    ],
)
def test_metadata_limits(data):
    with pytest.raises(ValidationError):
        ProjectMetadata.model_validate(data)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def make_store(**kwargs) -> tuple[SessionStore, Clock]:
    clock = Clock()
    options = {"max_turns": 6, "ttl": timedelta(minutes=30), "max_sessions": 10} | kwargs
    return SessionStore(clock=clock, **options), clock


def test_store_create_get_delete():
    store, _ = make_store()
    session = store.create()
    assert session.session_id.version == 4
    assert store.get(session.session_id) is session
    assert store.delete(session.session_id) is True
    assert store.get(session.session_id) is None


def test_sessions_expire_after_inactivity_and_access_renews_them():
    store, clock = make_store()
    session = store.create()

    clock.now += timedelta(minutes=20)
    assert store.get(session.session_id) is session  # renueva last_access_at
    clock.now += timedelta(minutes=20)
    assert store.get(session.session_id) is session
    clock.now += timedelta(minutes=31)
    assert store.get(session.session_id) is None
    assert len(store) == 0


def test_max_sessions_rejects_instead_of_evicting():
    store, clock = make_store(max_sessions=2)
    first, second = store.create(), store.create()
    with pytest.raises(SessionCapacityError):
        store.create()
    assert store.get(first.session_id) and store.get(second.session_id)

    clock.now += timedelta(hours=1)  # caducan: vuelve a haber hueco
    store.create()
    assert len(store) == 1
