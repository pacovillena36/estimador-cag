"""Fase 2: POST/GET/DELETE /sessions."""

import uuid

import pytest

from tests.sessions.conftest import create_session

pytestmark = pytest.mark.anyio


async def test_create_session_returns_201_with_uuid4(client, store):
    response = await client.post("/api/v1/sessions")
    assert response.status_code == 201
    session_id = uuid.UUID(response.json()["session_id"])
    assert session_id.version == 4
    assert len(store) == 1


async def test_get_session_returns_empty_metadata_and_zero_turns(client):
    session_id = await create_session(client)
    response = await client.get(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "project_metadata": {
            "project_name": None,
            "assumed_team_size": None,
            "mentioned_technologies": [],
            "agreed_scope": None,
        },
        "turns": 0,
    }


async def test_delete_session(client, store):
    session_id = await create_session(client)
    assert (await client.delete(f"/api/v1/sessions/{session_id}")).status_code == 204
    assert len(store) == 0
    assert (await client.get(f"/api/v1/sessions/{session_id}")).status_code == 404


async def test_invalid_session_id_is_422_and_unknown_is_404(client):
    assert (await client.get("/api/v1/sessions/no-es-un-uuid")).status_code == 422

    response = await client.get(f"/api/v1/sessions/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


@pytest.mark.parametrize("session_settings", [{"max_sessions": 1}])
async def test_max_sessions_returns_503(client):
    await create_session(client)
    response = await client.post("/api/v1/sessions")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "sessions_exhausted"
