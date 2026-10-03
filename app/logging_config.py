"""Configuración de logging estructurado con structlog.

Todos los logs (los de la app vía structlog y los de librerías vía el
módulo estándar `logging`, p. ej. uvicorn o LiteLLM) pasan por la misma
cadena de procesadores, así que salen con el mismo formato y con el
contexto de la petición (request_id) enlazado mediante contextvars.
"""

import logging
import sys

import structlog

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
        shared_processors.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # uvicorn instala sus propios handlers; los quitamos para que sus logs
    # propaguen al root y salgan con el mismo formato estructurado.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # LiteLLM en DEBUG/INFO puede volcar el contenido de los mensajes
    # (transcripciones del cliente): lo limitamos a avisos y errores.
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Instructor registra en ERROR el mensaje completo de cada fallo (del
    # proveedor o de validación, que puede citar la salida del modelo). El
    # wrapper ya registra esos fallos sin contenido (llm.provider_failed,
    # llm.validation_failed), así que se silencia.
    logging.getLogger("instructor").setLevel(logging.CRITICAL)
