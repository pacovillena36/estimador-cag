"""Punto de entrada de la aplicación FastAPI."""

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import estimations

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


class UTF8JSONResponse(JSONResponse):
    """JSONResponse con charset=utf-8 explícito en el Content-Type.

    Sin esto, algunos clientes HTTP (p. ej. Invoke-RestMethod de Windows
    PowerShell 5.1) asumen Latin-1 al no encontrar un charset declarado y
    corrompen los acentos de la respuesta.
    """

    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="Estimador CAG",
    description=(
        "API para generar estimaciones de proyectos de software a partir de "
        "transcripciones de reuniones, usando una arquitectura CAG "
        "(Cache-Augmented Generation): ejemplos de estimaciones previas se "
        "inyectan directamente como contexto en el prompt del LLM."
    ),
    version="0.1.0",
    default_response_class=UTF8JSONResponse,
)

app.include_router(estimations.router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
