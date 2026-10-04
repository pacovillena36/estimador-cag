"""Punto de entrada de la aplicación FastAPI."""

import re
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.guardrails.base import InputRejectedError, ModerationUnavailableError
from app.guardrails.moderation import get_moderation_client
from app.logging_config import configure_logging
from app.prompts.loader import PromptVersionNotFoundError, validate_estimation_prompt_version
from app.routers import estimations
from app.schemas import ErrorDetail, ErrorResponse
from app.services.llm_gateway import (
    AllProvidersFailedError,
    InvalidStructuredOutputError,
    get_llm_gateway,
)

configure_logging(settings)
log = structlog.get_logger(__name__)

# Solo se acepta un X-Request-ID entrante con formato seguro; si no, se
# genera uno nuevo (evita inyección de contenido arbitrario en los logs).
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Middleware ASGI puro (compatible con StreamingResponse): asigna un
    request_id a cada petición, lo enlaza al contexto de structlog para
    que aparezca en todos los logs de esa petición, lo devuelve en la
    cabecera X-Request-ID y registra método, ruta, estado y duración."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()


class UTF8JSONResponse(JSONResponse):
    """JSONResponse con charset=utf-8 explícito en el Content-Type.

    Sin esto, algunos clientes HTTP (p. ej. Invoke-RestMethod de Windows
    PowerShell 5.1) asumen Latin-1 al no encontrar un charset declarado y
    corrompen los acentos de la respuesta.
    """

    media_type = "application/json; charset=utf-8"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail fast: la API no arranca si no hay ningún proveedor con API key,
    # si falta la clave de la Moderation API o si la versión de prompt
    # configurada no existe. Los valores de Settings (tipos, rangos, modos
    # de guardrail) ya se validan al importar app.config.
    gateway = get_llm_gateway()
    get_moderation_client()
    validate_estimation_prompt_version(settings.prompt_version)
    log.info(
        "app.started",
        environment=settings.environment,
        llm_providers=gateway.provider_names,
        prompt_version=settings.prompt_version,
        guardrail_modes={
            "moderation": settings.guardrail_moderation_mode.value,
            "prompt_injection": settings.guardrail_injection_mode.value,
            "pii_input": settings.guardrail_pii_input_mode.value,
            "pii_output": settings.guardrail_pii_output_mode.value,
        },
        moderation_fail_closed=settings.moderation_fail_closed,
        min_confidence_pct=settings.min_confidence_pct,
    )
    yield


app = FastAPI(
    title="Estimador CAG",
    description=(
        "API para generar estimaciones de proyectos de software a partir de "
        "la descripción del proyecto, su tipo, el nivel de detalle y el "
        "formato de salida deseados. Los prompts son templates Jinja2 "
        "versionados con ejemplos few-shot, la respuesta del modelo es una "
        "estimación estructurada validada con Pydantic (Instructor) y la "
        "petición pasa por un pipeline de guardrails de input y de output."
    ),
    version="0.5.0",
    default_response_class=UTF8JSONResponse,
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.include_router(estimations.router, prefix="/api/v1")


# ------------------------------------------------------------------ errores
#
# Todos los errores se traducen aquí, con un cuerpo único (ErrorResponse) y
# mensajes genéricos. El detalle interno (proveedor, guardrail o patrón que
# disparó, trazas) va solo a los logs, nunca al cliente: le enseñaría a un
# atacante cómo esquivar los filtros.


def _request_id() -> str | None:
    return structlog.contextvars.get_contextvars().get("request_id")


def _error(status_code: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=_request_id()))
    return UTF8JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


@app.exception_handler(RequestValidationError)
async def _request_validation_failed(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Formato estándar de FastAPI ({"detail": [...]}) pero sin el campo
    # "input": no se devuelve el eco de lo enviado (p. ej. la descripción).
    errors = [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]
    log.info(
        "request.invalid",
        errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in errors],
    )
    return UTF8JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": errors, "request_id": _request_id()}),
    )


@app.exception_handler(InputRejectedError)
async def _input_rejected(_: Request, exc: InputRejectedError) -> JSONResponse:
    # El guardrail ya ha registrado qué disparó (guardrail.evaluated).
    log.warning("estimation.input_rejected", guardrail=exc.guardrail)
    return _error(400, "input_rejected", "No se ha podido procesar la descripción.")


@app.exception_handler(ModerationUnavailableError)
async def _moderation_unavailable(_: Request, __: ModerationUnavailableError) -> JSONResponse:
    return _error(
        503,
        "moderation_unavailable",
        "El servicio no está disponible temporalmente. Inténtalo más tarde.",
        headers={"Retry-After": "30"},
    )


@app.exception_handler(AllProvidersFailedError)
async def _all_providers_failed(_: Request, __: AllProvidersFailedError) -> JSONResponse:
    return _error(
        503,
        "llm_unavailable",
        "El servicio de IA no está disponible temporalmente. Inténtalo más tarde.",
        headers={"Retry-After": "30"},
    )


# El detalle de validación ya lo registra el wrapper (llm.validation_failed).
@app.exception_handler(InvalidStructuredOutputError)
async def _invalid_estimation(_: Request, exc: InvalidStructuredOutputError) -> JSONResponse:
    log.warning("estimation.invalid", reason=str(exc))
    return _error(502, "estimation_failed", "El modelo no devolvió una estimación válida. Inténtalo de nuevo.")


# La versión configurada se valida al arrancar, así que este error solo
# llega por una ?prompt_version= pedida por el cliente que no existe.
@app.exception_handler(PromptVersionNotFoundError)
async def _prompt_version_not_found(_: Request, exc: PromptVersionNotFoundError) -> JSONResponse:
    log.info("estimation.unknown_prompt_version", reason=str(exc))
    return _error(422, "invalid_prompt_version", "La versión de prompt pedida no existe.")


@app.exception_handler(Exception)
async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # Cualquier otro error: traza en los logs, mensaje genérico al cliente.
    log.exception("request.unhandled_error", error_type=type(exc).__name__)
    return _error(500, "internal_error", "Error interno. Inténtalo de nuevo más tarde.")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
