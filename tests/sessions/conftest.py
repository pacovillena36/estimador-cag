"""Fixtures de las sesiones: SessionStore nuevo por test, settings herméticos
y un cliente httpx asíncrono contra la app (ASGITransport, sin red)."""

from datetime import timedelta

import httpx
import pytest

from app.config import get_settings
from app.dependencies import get_session_store
from app.main import app
from app.sessions import SessionStore
from tests.conftest import make_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def session_settings():
    return {}


@pytest.fixture
def settings(session_settings):
    return make_settings(**session_settings)


@pytest.fixture
def store(settings) -> SessionStore:
    return SessionStore(
        max_turns=settings.max_turns,
        ttl=timedelta(minutes=settings.session_ttl_minutes),
        max_sessions=settings.max_sessions,
    )


@pytest.fixture
def overrides(settings, store) -> dict:
    """Dependencias sustituidas para cada test; los módulos de fases
    posteriores añaden aquí el LLM falso."""
    return {get_settings: lambda: settings, get_session_store: lambda: store}


@pytest.fixture
async def client(overrides):
    app.dependency_overrides.update(overrides)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def create_session(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/v1/sessions")
    assert response.status_code == 201
    return response.json()["session_id"]
