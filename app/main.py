"""Punto de entrada de la aplicación FastAPI."""

import re
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.logging_config import configure_logging
from app.prompts.loader import PromptVersionNotFoundError, validate_estimation_prompt_version
from app.routers import estimations
from app.routers.estimations import InvalidEstimationError
from app.services.llm_gateway import AllProvidersFailedError, get_llm_gateway

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
    # Fail fast: la API no arranca si no hay ningún proveedor con API key
    # o si la versión de prompt configurada no existe.
    gateway = get_llm_gateway()
    validate_estimation_prompt_version(settings.prompt_version)
    log.info(
        "app.started",
        environment=settings.environment,
        llm_providers=gateway.provider_names,
        prompt_version=settings.prompt_version,
    )
    yield


app = FastAPI(
    title="Estimador CAG",
    description=(
        "API para generar estimaciones de proyectos de software a partir de "
        "la descripción del proyecto, su tipo, el nivel de detalle y el "
        "formato de salida deseados. Los prompts son templates Jinja2 "
        "versionados con ejemplos few-shot."
    ),
    version="0.3.0",
    default_response_class=UTF8JSONResponse,
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.include_router(estimations.router, prefix="/api/v1")


# Los errores del LLM se traducen a respuestas genéricas: el detalle
# (proveedor, código, mensaje) queda en los logs, nunca en la respuesta.
@app.exception_handler(AllProvidersFailedError)
async def _all_providers_failed(_: Request, __: AllProvidersFailedError) -> JSONResponse:
    return UTF8JSONResponse(
        status_code=503,
        content={"detail": "El servicio de IA no está disponible temporalmente. Inténtalo más tarde."},
        headers={"Retry-After": "30"},
    )


@app.exception_handler(InvalidEstimationError)
async def _invalid_estimation(_: Request, exc: InvalidEstimationError) -> JSONResponse:
    log.warning("estimation.invalid", reason=str(exc))
    return UTF8JSONResponse(
        status_code=502,
        content={"detail": "El modelo no devolvió una estimación válida. Inténtalo de nuevo."},
    )


# La versión configurada se valida al arrancar, así que este error solo
# llega por una ?prompt_version= pedida por el cliente que no existe.
@app.exception_handler(PromptVersionNotFoundError)
async def _prompt_version_not_found(_: Request, exc: PromptVersionNotFoundError) -> JSONResponse:
    log.info("estimation.unknown_prompt_version", reason=str(exc))
    return UTF8JSONResponse(
        status_code=422,
        content={"detail": "La versión de prompt pedida no existe."},
    )


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
