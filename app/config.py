from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    llm_timeout_seconds: float = 60.0
    # Reintentos sobre el MISMO proveedor antes de rotar al siguiente.
    llm_max_retries: int = 1
    # Salida estructurada (Instructor): veces que se reenvía al modelo su
    # respuesta con los errores de validación antes de darla por inválida.
    llm_validation_retries: int = 2
    llm_max_tokens: int = 2048

    # SecretStr evita que las claves aparezcan en repr(), logs o trazas.
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_workspace_id: str | None = None
    openai_model: str = "gpt-4o-mini"
    # Snapshot fechado en lugar del alias: el mismo prompt da resultados
    # reproducibles aunque Anthropic actualice el alias "claude-haiku-4-5".
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Versión de los prompts (carpeta app/prompts/estimation/<versión>/).
    prompt_version: str = "v1"

    # Caché exact-match de respuestas del LLM (en memoria, por proceso)
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = 3600
    llm_cache_max_entries: int = 256


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
