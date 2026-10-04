from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.guardrails.base import GuardrailMode
from app.semantic_cache.ports import CacheMode
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

    # Caché semántico (Redis Stack + redisvl). off | shadow | active.
    # shadow (por defecto): calcula embedding, consulta y escribe, pero
    # siempre responde con el LLM; sirve para medir antes de activarlo.
    semantic_cache_mode: CacheMode = CacheMode.SHADOW
    # Obligatoria si el modo no es off. Fuera de local: rediss:// (TLS) con
    # usuario ACL y contraseña.
    redis_url: SecretStr | None = None
    semantic_cache_index_name: str = Field("estimation_cache", pattern=r"^[A-Za-z0-9_:-]{1,64}$")
    # Distancia coseno máxima para un hit (0.08 ≈ similitud >= 0.92).
    semantic_cache_distance_threshold: float = Field(0.08, gt=0, le=0.3)
    # Retención: entre 5 minutos y 30 días.
    semantic_cache_ttl_seconds: int = Field(86_400, ge=300, le=30 * 86_400)
    # El caché no debe añadir más latencia de la que ahorra.
    redis_socket_timeout_ms: int = Field(150, ge=10, le=5_000)

    # Embeddings (OpenAI) para el caché semántico. La dimensión debe
    # coincidir con la del índice de Redis.
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(1536, ge=64, le=3072)
    embedding_timeout_ms: int = Field(1000, ge=100, le=10_000)

    # --- Sesiones conversacionales (POST /sessions/{id}/estimate) ----------
    # Versión del prompt de las sesiones (v4 = v3 + <project_metadata> y los
    # bloques <transcript>/<attachment_content>). /estimate sigue con
    # PROMPT_VERSION.
    session_prompt_version: str = Field("v4", pattern=r"^v\d+$")
    # Ventana deslizante: pares user+assistant previos que se envían al LLM.
    max_turns: int = Field(6, ge=1, le=50)
    # Memoria del proceso: expiración por inactividad y número máximo de
    # sesiones vivas (al llegar al máximo, POST /sessions responde 503).
    session_ttl_minutes: int = Field(60, ge=1, le=24 * 60)
    max_sessions: int = Field(1000, ge=1, le=100_000)
    max_transcript_chars: int = Field(20_000, ge=20, le=200_000)

    # Adjuntos (PDF y DOCX), procesados en memoria.
    max_attachments: int = Field(5, ge=0, le=20)
    max_attachment_bytes: int = Field(10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_total_attachment_bytes: int = Field(25 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)
    max_pdf_pages: int = Field(50, ge=1, le=1000)
    # DOCX es un ZIP: límites contra zip bombs antes de abrirlo.
    max_docx_uncompressed_bytes: int = Field(50 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    max_docx_entries: int = Field(1000, ge=10, le=10_000)
    # Texto extraído (por adjunto y total); lo que exceda se trunca.
    max_attachment_chars: int = Field(20_000, ge=100, le=500_000)
    max_total_attachment_chars: int = Field(50_000, ge=100, le=1_000_000)

    # Extractor de project_metadata (segunda llamada al LLM por turno).
    metadata_extractor_max_tokens: int = Field(512, ge=64, le=4096)

    # Caché exact-match de respuestas del LLM (en memoria, por proceso)
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = 3600
    llm_cache_max_entries: int = 256


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
