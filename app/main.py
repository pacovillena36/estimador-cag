"""Punto de entrada de la aplicación FastAPI."""

from fastapi import FastAPI

from app.routers import estimations

app = FastAPI(
    title="Estimador CAG",
    description=(
        "API para generar estimaciones de proyectos de software a partir de "
        "transcripciones de reuniones, usando una arquitectura CAG "
        "(Cache-Augmented Generation): ejemplos de estimaciones previas se "
        "inyectan directamente como contexto en el prompt del LLM."
    ),
    version="0.1.0",
)

app.include_router(estimations.router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
