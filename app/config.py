from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.guardrails.base import GuardrailMode
from app.schemas import (
    DEFAULT_MIN_CONFIDENCE_PCT,
    DESCRIPTION_HARD_MAX_LENGTH,
    DESCRIPTION_MIN_LENGTH,
)

ProviderName = Literal["openai", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "estimador-cag"
    environment: str = "development"
    debug: bool = True
    port: int = 8000
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # "console" (legible, para desarrollo) o "json" (una línea JSON por
    # evento, para producción / agregadores de logs)
    log_format: Literal["console", "json"] = "console"

    # LLM
    # Proveedor preferido; si falla y el fallback está activo, el wrapper
    # rota al otro proveedor (siempre que tenga API key configurada).
    llm_provider: ProviderName = "anthropic"
    llm_fallback_enabled: bool = True
    llm_timeout_seconds: float = Field(60.0, gt=0, le=300)
    # Reintentos sobre el MISMO proveedor antes de rotar al siguiente.
    llm_max_retries: int = Field(1, ge=0, le=5)
    # Salida estructurada (Instructor): veces que se reenvía al modelo su
    # respuesta con los errores de validación antes de darla por inválida.
    llm_validation_retries: int = Field(2, ge=0, le=5)
    llm_max_tokens: int = Field(2048, ge=256, le=16384)

    # SecretStr evita que las claves aparezcan en repr(), logs o trazas.
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_workspace_id: str | None = None
    openai_model: str = "gpt-4o-mini"
    # Snapshot fechado en lugar del alias: el mismo prompt da resultados
    # reproducibles aunque Anthropic actualice el alias "claude-haiku-4-5".
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Versión de los prompts (carpeta app/prompts/estimation/<versión>/).
    # v3 = v1 + sección de alcance (permite responder "fuera de alcance").
    prompt_version: str = "v3"

    # Guardrails: modo de despliegue de cada uno (log_only | enforce). Las
    # heurísticas nuevas arrancan en log_only: primero se observan sus
    # disparos en los logs y, una vez ajustadas, se pasan a enforce.
    guardrail_moderation_mode: GuardrailMode = GuardrailMode.ENFORCE
    guardrail_injection_mode: GuardrailMode = GuardrailMode.LOG_ONLY
    guardrail_pii_input_mode: GuardrailMode = GuardrailMode.LOG_ONLY
    guardrail_pii_output_mode: GuardrailMode = GuardrailMode.LOG_ONLY

    # Moderation API (OpenAI). Clave separada y de permisos mínimos; si no
    # se define se usa OPENAI_API_KEY. Hace falta aunque el LLM sea Anthropic.
    moderation_api_key: SecretStr | None = None
    moderation_model: str = "omni-moderation-latest"
    moderation_timeout_s: float = Field(5.0, gt=0, le=30)
    # Decisión de producto: si la Moderation API no responde, true rechaza
    # la petición con 503 (fail-closed); false la deja pasar sin moderar
    # (fail-open) y lo registra.
    moderation_fail_closed: bool = True

    # Umbral de producto: una estimación con menos confianza se devuelve
    # como fuera de alcance (todo a 0).
    min_confidence_pct: int = Field(DEFAULT_MIN_CONFIDENCE_PCT, ge=0, le=100)
    # Longitud máxima de la descripción (protege del abuso de coste).
    description_max_length: int = Field(
        2000, ge=DESCRIPTION_MIN_LENGTH, le=DESCRIPTION_HARD_MAX_LENGTH
    )

    # Caché exact-match de respuestas del LLM (en memoria, por proceso)
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = 3600
    llm_cache_max_entries: int = 256


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
