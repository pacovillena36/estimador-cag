"""Moderation API de OpenAI (capa 2, política EXCEPTION, ENFORCE por defecto).

- Umbral inicial conservador: se bloquea si la API marca `flagged=True`. Los
  scores por categoría se registran para poder ajustar el umbral después.
- Timeout corto (MODERATION_TIMEOUT_S) y sin reintentos: no debe añadir
  segundos de latencia a cada estimación.
- Si la propia API falla (timeout, red, error HTTP) se eleva
  ModerationUnavailableError. Qué hacer entonces es una decisión de
  producto (MODERATION_FAIL_CLOSED, ver pipeline.py): por defecto
  fail-closed, la petición se rechaza con 503.
- Necesita una API key de OpenAI aunque el LLM principal sea Anthropic.
  Usa una clave separada y de permisos mínimos (MODERATION_API_KEY); si no
  se define, se usa OPENAI_API_KEY.
"""

from functools import lru_cache

import openai
from pydantic import SecretStr

from app.config import get_settings
from app.guardrails.base import FailurePolicy, GuardrailResult, ModerationUnavailableError

NAME = "moderation"


class ModerationNotConfiguredError(RuntimeError):
    """No hay API key para la Moderation API (la API no arranca)."""


class ModerationClient:
    def __init__(self, api_key: SecretStr, *, model: str, timeout_s: float) -> None:
        self._model = model
        self._client = openai.OpenAI(
            api_key=api_key.get_secret_value(),
            timeout=timeout_s,
            # Sin reintentos: si falla, decide la política fail-closed/open.
            max_retries=0,
        )

    def check(self, text: str) -> GuardrailResult:
        try:
            response = self._client.moderations.create(model=self._model, input=text)
        except openai.APIError as exc:  # timeout, conexión y errores HTTP
            raise ModerationUnavailableError(type(exc).__name__) from exc

        result = response.results[0]
        scores = {
            name: round(score, 4)
            for name, score in result.category_scores.model_dump(by_alias=True).items()
            if score is not None
        }
        flagged = sorted(
            name for name, value in result.categories.model_dump(by_alias=True).items() if value
        )
        return GuardrailResult(
            name=NAME,
            triggered=result.flagged,
            policy=FailurePolicy.EXCEPTION,
            score=max(scores.values(), default=None),
            internal_detail=",".join(flagged) or None,
            scores=scores,
        )


@lru_cache
def get_moderation_client() -> ModerationClient:
    """Instancia única, inyectada con Depends (sustituible en tests). No
    hace ninguna llamada de red al crearse."""
    settings = get_settings()
    api_key = settings.moderation_api_key or settings.openai_api_key
    if not api_key or not api_key.get_secret_value():
        raise ModerationNotConfiguredError(
            "La Moderation API necesita MODERATION_API_KEY u OPENAI_API_KEY."
        )
    return ModerationClient(
        api_key,
        model=settings.moderation_model,
        timeout_s=settings.moderation_timeout_s,
    )
