"""Transporte del stress test: un cliente de la API de sesiones.

Los dos modos usan httpx.AsyncClient y solo cambia el transporte, así el
orquestador no sabe cuál usa:
- --http URL: red real contra la API (p. ej. docker compose).
- in-process: httpx.ASGITransport sobre app.main.app (el mismo patrón que
  tests/sessions; equivale a TestClient pero asíncrono). Llama al LLM real
  con la configuración del .env.

Reintentos acotados con backoff solo ante errores de red y 5xx; nunca ante
4xx (son errores del propio test y repetirlos no cambia nada).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

API = "/api/v1/sessions"
TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0)


@dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes


@dataclass(frozen=True)
class ApiResult:
    status: int | None  # None: error de red tras agotar los reintentos
    body: dict[str, Any] | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


class SessionApiClient:
    def __init__(self, http: httpx.AsyncClient, *, retries: int = 2, backoff_seconds: float = 2.0) -> None:
        self._http = http
        self._retries = retries
        self._backoff = backoff_seconds

    async def create_session(self) -> ApiResult:
        return await self._send("POST", API)

    async def estimate(
        self, session_id: str, transcript: str, project_type: str, attachment: Attachment | None
    ) -> ApiResult:
        data = {"transcript": transcript, "project_type": project_type}
        files = (
            [("attachments", (attachment.filename, attachment.data, "application/pdf"))] if attachment else None
        )
        return await self._send("POST", f"{API}/{session_id}/estimate", data=data, files=files)

    async def state(self, session_id: str) -> ApiResult:
        return await self._send("GET", f"{API}/{session_id}")

    async def _send(self, method: str, url: str, **kwargs: Any) -> ApiResult:
        last_error = "unknown"
        for attempt in range(self._retries + 1):
            if attempt:
                await asyncio.sleep(self._backoff * attempt)
            try:
                response = await self._http.request(method, url, **kwargs)
            except httpx.TransportError as exc:  # red, timeouts
                last_error = type(exc).__name__
                continue
            body = _json(response)
            if response.status_code >= 500 and attempt < self._retries:
                last_error = f"http_{response.status_code}"
                continue
            error = None if response.is_success else _error_code(body, response.status_code)
            return ApiResult(response.status_code, body, error)
        return ApiResult(None, None, last_error)


def _json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _error_code(body: dict[str, Any] | None, status: int) -> str:
    error = (body or {}).get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return f"http_{status}:{code}" if code else f"http_{status}"


@asynccontextmanager
async def http_client(base_url: str) -> AsyncIterator[SessionApiClient]:
    async with httpx.AsyncClient(base_url=base_url, timeout=TIMEOUT) as http:
        yield SessionApiClient(http)


@asynccontextmanager
async def in_process_client() -> AsyncIterator[SessionApiClient]:
    """La app en el mismo proceso, con los flags de depuración activados
    solo aquí (sustituyendo get_settings, sin tocar el .env)."""
    from app.config import get_settings
    from app.main import app

    settings = get_settings().model_copy(
        update={"expose_turn_observation": True, "expose_session_memory_debug": True}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://in-process", timeout=TIMEOUT) as http:
            yield SessionApiClient(http)
    finally:
        app.dependency_overrides.pop(get_settings, None)
