# estimador-cag

Servicio IA en FastAPI que genera estimaciones de esfuerzo de proyectos de
software a partir de su descripción, con un cliente web en Streamlit. Los
prompts son templates Jinja2 versionados con ejemplos few-shot, y las
llamadas al LLM pasan por un wrapper de proveedores (OpenAI / Anthropic)
con fallback, caché y logging estructurado. La respuesta del modelo es una
**salida estructurada**: una instancia de un modelo Pydantic validada con
[Instructor](https://python.useinstructor.com/), nunca texto libre que haya
que interpretar.

## Estructura del proyecto

```
estimador-cag/
├── .github/
│   └── workflows/
│       └── validate.yml          # CI: estructura, tests y que /health responda 200
├── app/
│   ├── __init__.py
│   ├── main.py                   # App FastAPI: middleware request_id, errores, lifespan
│   ├── config.py                 # Configuración (pydantic-settings, claves como SecretStr)
│   ├── schemas.py                # Contrato cliente-servicio y con el LLM (Pydantic v2)
│   ├── logging_config.py         # Logging estructurado con structlog
│   ├── guardrails/
│   │   ├── base.py               # FailurePolicy, GuardrailMode, registro GUARDRAILS
│   │   ├── normalize.py          # NFKC + casefold, sin invisibles (antes de heurísticas)
│   │   ├── moderation.py         # Moderation API de OpenAI
│   │   ├── injection.py          # Patrones de prompt injection (ES/EN)
│   │   ├── pii.py                # Emails, teléfonos e IBAN (detección y redacción)
│   │   └── pipeline.py           # Pipelines de input y output, logging
│   ├── semantic_cache/
│   │   ├── ports.py              # Protocol EstimationCache, CacheMode
│   │   ├── bucket.py             # build_bucket_key (pura)
│   │   ├── normalization.py      # normalize_description (pura)
│   │   ├── redis_cache.py        # Adaptador Redis Stack + redisvl (SemanticCache)
│   │   ├── noop.py               # Caché nulo (modo off)
│   │   └── factory.py            # get_semantic_cache() según la configuración
│   ├── embeddings.py             # Cliente de embeddings (OpenAI) con timeout
│   ├── request_context.py        # RequestContext (tenant) fuera del body
│   ├── sessions.py               # ProjectMetadata, ConversationHistory, Session, SessionStore
│   ├── attachments.py            # Validación y extracción de texto de PDF/DOCX
│   ├── metadata_extractor.py     # Extractor LLM de project_metadata + merge
│   ├── dependencies.py           # get_session_store, servicios de sesión (inyectables)
│   ├── prompts/
│   │   ├── loader.py             # render_estimation_prompt(request, version) -> (system, user)
│   │   └── estimation/
│   │       ├── v1/
│   │       │   ├── system.j2     # Rol, criterio de estimación y pistas de contenido
│   │       │   ├── user.j2       # Envuelve la descripción en <project_description>
│   │       │   └── examples.j2   # Ejemplos few-shot (instancias de EstimationResult en JSON)
│   │       ├── v2/               # Igual que v1 salvo el set de ejemplos
│   │       ├── v4/               # Sesiones: <transcript>, <attachment_content>, <project_metadata>
│   │       └── v3/               # v1 + sección <scope> (versión por defecto)
│   ├── routers/
│   │   └── estimations.py        # POST /api/v1/estimate (delgado: delega en el servicio)
│   └── services/
│       ├── estimation_service.py # Orquesta guardrails, prompt y LLM
│       ├── llm_gateway.py        # Wrapper de proveedores (LiteLLM + Instructor): fallback, caché, logging
│       └── cache.py              # Caché exact-match en memoria (TTL + LRU)
├── tests/
│   ├── guardrails/               # Normalización, injection, PII, moderación, pipeline
│   ├── semantic_cache/           # Bucket, servicio con dobles, adaptadores e integración (Redis Stack)
│   ├── sessions/                 # Sesiones: modelo, API, adjuntos, metadata y turnos con LLM falso
│   ├── prompts/
│   │   ├── test_estimation_v1.py # Tests de los templates (sin llamar a ningún modelo)
│   │   └── test_estimation_v2.py
│   ├── test_api.py
│   ├── test_estimation_view.py
│   ├── test_llm_gateway.py
│   ├── test_llm_logging.py
│   └── test_schemas.py           # Contrato EstimationResult y sus validadores
├── .env.example
├── Dockerfile                     # Imagen única (Python 3.12 + uv) para API y cliente
├── docker-compose.yml             # Servicios "api" (uvicorn) y "chat" (Streamlit)
├── ejemplo_peticion.json          # Petición de ejemplo para probar el endpoint
├── pyproject.toml
├── streamlit_app.py               # Cliente Streamlit (conversación con sesiones), cliente HTTP de la API
├── estimation_view.py             # Presentación en el cliente: tabla, lista o narrativa
└── README.md
```

## Requisitos

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)

## Instalación

```bash
uv sync
```

## Configuración

Copia `.env.example` a `.env` y completa las variables necesarias (claves de API, etc.).

```bash
cp .env.example .env
```

Hace falta al menos una API key (`OPENAI_API_KEY` o `ANTHROPIC_API_KEY`):
la API no arranca si no hay ningún proveedor configurado. Con las dos, el
wrapper puede rotar de uno a otro si el preferido (`LLM_PROVIDER`) falla.
La Moderation API necesita además una clave de OpenAI (`MODERATION_API_KEY`
u `OPENAI_API_KEY`) aunque el LLM sea Anthropic.

Toda la configuración se valida al arrancar (tipos, rangos y modos de
guardrail); si falta algo obligatorio, la API no arranca.

## Contrato cliente-servicio

Definido con Pydantic v2 en [`app/schemas.py`](app/schemas.py) y reutilizado
por el cliente Streamlit para validar el formulario antes de enviarlo.

**`EstimationRequest`**

| Campo | Tipo | Valores |
|---|---|---|
| `description` | `str` | 20 a `DESCRIPTION_MAX_LENGTH` caracteres (2000 por defecto; techo absoluto 10 000). Se recortan espacios al inicio/fin |
| `project_type` | `ProjectType` | `mobile_app`, `web_saas`, `internal_tool`, `data_pipeline` |
| `detail_level` | `DetailLevel` | `summary`, `medium`, `detailed` |
| `output_format` | `OutputFormat` | `phases_table`, `line_items`, `narrative` |

Cualquier otro campo se rechaza (`extra="forbid"`).

**`EstimationResponse`**: `result` (un `EstimationResult`),
`prompt_version` (versión del prompt usada, p. ej. `v3`) y `out_of_scope`
(calculado en el servidor: `true` si el modelo no pudo estimar) y `cached`
(`true` si se sirvió del caché semántico sin llamar al LLM). Los
consumidores deben usar `out_of_scope`, nunca interpretar el `summary`.

**`EstimationResult`**: la estimación estructurada. Es a la vez el contrato
con el LLM (Instructor envía su JSON Schema al proveedor), la documentación
OpenAPI del endpoint (`/docs`) y el tipo que circula por el código: el
shape no se repite a mano en ningún otro sitio, tampoco en los prompts.

| Campo | Tipo | Restricciones |
|---|---|---|
| `summary` | `str` | empieza por `Out of scope:` si el proyecto está fuera de alcance |
| `total_hours` | `int` | ≥ 0; igual a la suma de `hours` de las fases |
| `total_duration_weeks` | `int` | ≥ 0 (≥ 1 en alcance); suma de `duration_weeks` de las fases (±1 semana) |
| `total_cost_eur` | `int` | ≥ 0; suma de `cost_eur` de las fases (±5 %) |
| `confidence_pct` | `int` | 0 a 100; en alcance, ≥ `MIN_CONFIDENCE_PCT` (30) |
| `phases` | `list[Phase]` | 0 a 20; al menos una en alcance |

Un resultado **fuera de alcance** tiene siempre `phases=[]` y todos los
totales y `confidence_pct` a 0. Una estimación en alcance con menos
confianza que `MIN_CONFIDENCE_PCT` no es válida: el modelo debe devolverla
como fuera de alcance. El umbral llega al validador por el contexto de
validación (el cliente, que no conoce la configuración del servidor, no lo
aplica al revalidar).

Cada `Phase`: `name`, `hours` (≥ 1), `duration_weeks` (1 a 52), `cost_eur`
(≥ 0), `confidence_pct` (0 a 100) y `assumptions` (`list[str]`). Las fases
son consecutivas, por eso la duración total es su suma. Los mensajes de
error de los validadores están en inglés a propósito: Instructor se los
reenvía al modelo en los reintentos.

El servicio devuelve siempre esta forma, nunca la presentación:
`output_format` y `detail_level` son solo pistas de contenido para el
modelo (extensión del `summary`, cantidad de asunciones). Cómo se pinta
(tabla, lista, narrativa, PDF...) lo decide el cliente.

## Prompts versionados (Jinja2)

Los prompts viven en `app/prompts/<nombre>/<versión>/` y se renderizan con
[`app/prompts/loader.py`](app/prompts/loader.py):

```python
system, user = render_estimation_prompt(request, version="v1")
```

- **`system.j2`**: rol del modelo, criterio de estimación (con una pauta
  específica por `project_type`) y pistas de contenido según
  `detail_level` y `output_format`. No describe la estructura de la
  respuesta (eso lo hace el schema). Incluye `examples.j2` con
  `{% include %}`.
- **`user.j2`**: envuelve la descripción del proyecto en
  `<project_description>...</project_description>`.
- **`examples.j2`**: dos ejemplos few-shot, renderizados como instancias
  de `EstimationResult` en JSON. Coste (horas × tarifa) y totales se
  calculan en el template, y el nivel de detalle recorta el resumen y las
  asunciones como piden las instrucciones. Un test valida cada ejemplo
  contra `EstimationResult`, así que nunca enseñan al modelo una respuesta
  que el contrato rechazaría.

El entorno de Jinja2 usa `StrictUndefined` (una variable que falte es un
error, no un hueco vacío en el prompt), `trim_blocks` y `lstrip_blocks`.

**Versiones disponibles**:

| Versión | Cambio |
|---|---|
| `v1` | Versión base. Ejemplos few-shot: app móvil y pipeline de datos. |
| `v2` | Variación deliberada **solo en los ejemplos**: un SaaS web y una herramienta interna (los `project_type` que v1 no cubre). `system.j2` y `user.j2` son idénticos a v1, así que las diferencias de resultado se pueden atribuir a los ejemplos. |
| `v3` | **Por defecto.** v1 + sección `<scope>` (capa 3 de guardrails): permiso explícito para responder "fuera de alcance" en lugar de inventar, y un ejemplo few-shot fuera de alcance. El prefijo `Out of scope:` y el umbral de confianza llegan del loader (constantes compartidas con los validadores). |

La versión por defecto es `PROMPT_VERSION` (en `.env`; la API comprueba al
arrancar que existe). Cada petición puede elegir otra con el query param
`?prompt_version=v2` en `/estimate`, útil para
comparar versiones con la misma entrada. Una versión inexistente o con
formato no válido devuelve `422` sin llamar al modelo.

**Nueva versión**: copia `estimation/v1/` a `estimation/vN/` y edítala. El
resto del código no cambia.

**Seguridad frente a prompt injection**: la descripción va siempre en el
mensaje `user` (nunca en el `system`), delimitada por etiquetas, y el system
prompt indica tratarla solo como datos. Si el usuario escribe las propias
etiquetas `<project_description>` para "cerrar" el bloque, el loader las
neutraliza.

## Guardrails (defense in depth)

Cada petición a `/estimate` pasa por cinco capas. Cada una es barata para
los casos que captura y la combinación cubre el espacio:

| Capa | Tipo | Qué hace | Dónde |
|---|---|---|---|
| 1 | Sintáctica (input) | Tipos, enums, longitudes (`DESCRIPTION_MAX_LENGTH`), `extra="forbid"` | `app/schemas.py`, router |
| 2 | Semántica (input) | Moderation API, prompt injection, PII | `app/guardrails/` |
| 3 | Robustez del prompt | Sección `<scope>` con permiso para "no puedo estimar"; descripción delimitada y con `<`/`>` neutralizados | `app/prompts/estimation/v3/`, `loader.py` |
| 4 | Sintáctica (output) | Schema `EstimationResult` vía Instructor | `app/schemas.py` |
| 5 | Semántica (output) | Coherencia de totales, confianza mínima, out-of-scope a cero; PII en la respuesta | `app/schemas.py`, `app/guardrails/` |

**Cada guardrail declara su política de fallo** en el registro
`GUARDRAILS` de [`app/guardrails/base.py`](app/guardrails/base.py), y su
modo de despliegue se configura por settings, sin tocar código:

| Guardrail | Capa | Política | Modo por defecto | Setting |
|---|---|---|---|---|
| `moderation` | 2 | EXCEPTION (400) | ENFORCE | `GUARDRAIL_MODERATION_MODE` |
| `prompt_injection` | 2 | EXCEPTION (400) | LOG_ONLY | `GUARDRAIL_INJECTION_MODE` |
| `pii_input` | 2 | EXCEPTION (400) | LOG_ONLY | `GUARDRAIL_PII_INPUT_MODE` |
| `output_contract` | 4-5 | FIX_RETRY (Instructor; 502 si se agotan) | siempre | `LLM_VALIDATION_RETRIES` |
| `out_of_scope` | 5 | FILTER (200 con todo a 0) | siempre | `MIN_CONFIDENCE_PCT` |
| `pii_output` | 5 | FILTER (redacta con `[REDACTED]`) | LOG_ONLY | `GUARDRAIL_PII_OUTPUT_MODE` |

- **EXCEPTION** aborta con un error explícito; **FIX_RETRY** reenvía el
  error al modelo para que corrija; **FILTER** devuelve una respuesta
  segura.
- **LOG_ONLY** registra el disparo sin bloquear; **ENFORCE** aplica la
  política. Regla: primero logging, luego bloqueo. Las heurísticas nuevas
  (injection, PII) arrancan en LOG_ONLY porque tienen falsos positivos
  conocidos (p. ej. "system prompt" en la descripción de un chatbot): se
  observan sus disparos en los logs, se ajustan los patrones y solo
  entonces se pasan a `enforce`.
- **Normalización**: las heurísticas se aplican sobre el texto en NFKC +
  `casefold()`, sin caracteres de ancho cero y con los espacios colapsados
  (`IGNORE   previous`, `ig\u200bnore`, caracteres fullwidth...).
- **Moderation API** (`omni-moderation-latest`): bloquea si `flagged=true`
  y registra los scores por categoría para ajustar el umbral. Timeout corto
  (`MODERATION_TIMEOUT_S`, 5 s) y sin reintentos. **Decisión de producto**:
  si la API no responde, `MODERATION_FAIL_CLOSED=true` (por defecto)
  rechaza la petición con `503`; con `false` la deja pasar sin moderar y lo
  registra (`guardrail.unavailable`). Necesita una clave de OpenAI aunque
  el LLM sea Anthropic (`MODERATION_API_KEY`, o `OPENAI_API_KEY` si no se
  define); sin ella la API no arranca.
- **PII**: regex de emails, teléfonos (españoles e internacionales) e IBAN
  con checksum. La detección por regex es frágil (está documentado en el
  código): si el dominio lo exige, hay que sustituirla por un detector
  dedicado.
- **Fuera de alcance**: si la descripción no es de software, es demasiado
  vaga o la confianza sería menor que `MIN_CONFIDENCE_PCT` (30), el modelo
  responde con un `summary` que empieza por `Out of scope:`, `phases=[]` y
  todo a 0, y la API devuelve `200` con `out_of_scope=true`. El umbral es
  una decisión de producto: una estimación legítima pero muy incierta
  (p. ej. 25 %) se convierte en out of scope; hay que vigilarlo con los
  logs (`estimation.generated out_of_scope=true`).
- **Errores sin detalles**: un input rechazado devuelve siempre el mismo
  mensaje genérico, sin decir qué guardrail, patrón o categoría disparó
  (enseñaría a esquivar el filtro). El detalle va solo a los logs.

**Observabilidad.** Cada guardrail evaluado deja un log
`guardrail.evaluated` con `request_id`, `guardrail`, `triggered`, `mode`,
`policy`, `score`, `latency_ms` y, como mucho, la categoría o patrón que
disparó (`detail`). Nunca la descripción ni la PII: para correlacionar se
usa `description_sha256` (SHA-256 truncado). Además: `guardrail.unavailable`
(moderación caída), `guardrail.layer_completed` (latencia por capa),
`llm.validation_retry` (cada reintento de Instructor) y
`estimation.generated` con `out_of_scope`. El repo no tiene Prometheus ni
OpenTelemetry, así que las métricas se obtienen agregando estos logs.

## Caché semántico (Redis Stack + redisvl)

Muchas peticiones son reformulaciones de otras ya respondidas. El caché
semántico devuelve la estimación guardada de una petición **equivalente**
sin llamar al LLM (ahorra 5-15 s y el coste de la llamada). Es distinto de
la caché exact-match del wrapper (misma petición exacta, en memoria).

```
POST /estimate
  -> validación (capa 1) -> input guardrails (capa 2)      rechazo: 400/422, sin tocar caché ni LLM
  -> bucket + embedding de la descripción normalizada      (una sola vez por petición)
  -> lookup en Redis dentro del bucket (distancia <= umbral)
       hit válido y modo active -> respuesta con cached=true
       miss / error / shadow    -> prompt + LLM + output guardrails
  -> escritura en caché (último paso, solo si todo validó)  -> cached=false
```

**Clave compuesta.**

- **Bucket determinista**: `prompt_version`, `tenant_id`, `project_type`,
  `detail_level` y `output_format`, guardado como SHA-256 (sin escapes en
  el TAG y sin identificadores en claro en Redis). Dos peticiones solo
  comparten caché si coinciden en todo esto. Al promocionar una versión de
  prompt, los buckets de la anterior quedan huérfanos y caducan por TTL: no
  hay invalidación manual.
- **Parte vectorial**: embedding de la descripción normalizada (NFKC y
  espacios colapsados), con búsqueda por similitud **solo dentro del
  bucket**.
- **Tenant**: el servicio aún no es multi-tenant ni tiene autenticación, así
  que todas las peticiones usan un tenant constante (`single-tenant`, en
  [`app/request_context.py`](app/request_context.py)). Cuando haya
  autenticación, el tenant debe salir del contexto autenticado, nunca del
  body.

**Garantías.**

- **Input guardrails antes que el caché**: un hit nunca se salta la
  moderación ni los filtros de entrada.
- **Escritura solo tras los output guardrails** (schema, validadores de
  negocio, PII): si algo falla o se agotan los reintentos, no se escribe
  nada (evita envenenar el caché).
- **Re-validación de los hits**: Redis no es fuente de confianza. Cada hit
  se valida con `EstimationResult.model_validate_json` (con el umbral de
  confianza actual); si no valida, cuenta como miss
  (`result=invalid_entry`) y se sigue por el LLM.
- **Fail-open**: si Redis o la API de embeddings fallan o superan su
  timeout, la petición sigue por el LLM y se registra el error. Si Redis no
  está disponible al arrancar, la API arranca igual y vuelve a intentar
  conectar como mucho cada 30 s. Lo que sí impide arrancar es una
  configuración inválida (p. ej. modo `shadow`/`active` sin `REDIS_URL`).
- **Sin texto del usuario en Redis**: se guardan el vector, el bucket
  hasheado y la respuesta ya validada. El campo `prompt` que exige redisvl
  guarda un hash del vector, no la descripción.

**Modos** (`SEMANTIC_CACHE_MODE`):

| Modo | Embedding + lookup + escritura | Responde con |
|---|---|---|
| `off` | No | El LLM |
| `shadow` (por defecto) | Sí | **Siempre el LLM** (`cached=false`) |
| `active` | Sí | El caché si hay hit válido (`cached=true`); si no, el LLM |

**Cómo interpretar el modo shadow.** Es el despliegue inicial: mide sin
cambiar ninguna respuesta. En cada hit se registra
`semantic_cache.shadow_comparison` con la distancia, la diferencia de horas
totales entre lo cacheado y lo que acaba de responder el LLM
(`total_hours_diff_pct`) y si coinciden en out-of-scope. Tras una semana:

- La **tasa de hits** (`semantic_cache.lookup result=hit` frente a `miss`)
  dice cuánto se ahorraría.
- Si los hits con `total_hours_diff_pct` alto u `out_of_scope_match=false`
  se concentran en las distancias más altas, el umbral es demasiado
  permisivo: bájalo (`SEMANTIC_CACHE_DISTANCE_THRESHOLD`). Si apenas hay
  hits y las diferencias son pequeñas, se puede subir con cuidado.
- Con los datos, pasa a `SEMANTIC_CACHE_MODE=active`.

Ten en cuenta que el LLM no es determinista: dos llamadas con la misma
petición ya difieren algo (en las pruebas, entre un 10 y un 20 % en horas),
así que no esperes `total_hours_diff_pct=0`.

**Observabilidad** (logs estructurados, con `request_id`, `prompt_version`,
`project_type` y `cache_mode`; nunca la descripción):

| Evento | Campos |
|---|---|
| `semantic_cache.lookup` | `result` (`hit`, `miss`, `error`, `invalid_entry`), `distance`, `latency_ms`, `bucket` (prefijo del hash) |
| `semantic_cache.embedding` / `semantic_cache.embedding_failed` | `latency_ms`, `error_type` |
| `semantic_cache.store` | `result` (`ok`, `error`), `latency_ms` |
| `semantic_cache.shadow_comparison` | `distance`, `total_hours_diff_pct`, `out_of_scope_match`, ... |
| `estimation.generated` | `cached`, `llm_latency_ms`, ... |

El repo no tiene Prometheus ni OpenTelemetry: las métricas del spec (tasa
de hits, histograma de distancias, latencias) se obtienen agregando estos
logs. Si se añade un sistema de métricas, estos puntos son donde
instrumentar.

**Requisitos de Redis.** Redis Stack (o Redis 8 con el Query Engine) por
la búsqueda vectorial; `docker-compose.yml` levanta
`redis/redis-stack-server:7.4.0-v6`. Fuera de local:

- Sin exposición pública: red privada (en docker-compose no se publica
  ningún puerto de Redis).
- TLS (`rediss://`) y un usuario ACL con permisos mínimos sobre el prefijo
  del índice (`estimation_cache:*`) y los comandos `FT.*`, `HSET`, `HGETALL`
  y `EXPIRE`. La contraseña, en un gestor de secretos.
- `REDIS_URL` es un `SecretStr`: nunca aparece en logs ni en errores.

**Embeddings.** `text-embedding-3-small` de OpenAI con 1536 dimensiones
(Anthropic no ofrece API de embeddings, así que hace falta una clave de
OpenAI aunque el LLM sea Anthropic: `EMBEDDING_API_KEY` u
`OPENAI_API_KEY`). Timeout de 1 s y sin reintentos. Al arrancar se hace una
llamada de calentamiento con un texto fijo: sin ella, la primera conexión
(DNS, TLS) supera a menudo el timeout y las primeras peticiones pierden el
caché. **Si cambias de modelo o de dimensión, cambia también
`SEMANTIC_CACHE_INDEX_NAME`**: un índice existente con otra dimensión no se
sobrescribe (el caché queda no disponible y se registra el error).

**TTL.** `SEMANTIC_CACHE_TTL_SECONDS` (24 h por defecto) es la política de
retención. redisvl refresca el TTL de una entrada cada vez que da hit (TTL
deslizante): una entrada consultada a menudo vive más de 24 h desde su
creación. Las de versiones de prompt antiguas dejan de consultarse y
caducan.

## Sesión 05: memoria conversacional, project_metadata y adjuntos

Además de `POST /api/v1/estimate` (una sola petición, sin memoria), el
servicio admite **conversaciones**: una sesión acumula turnos (transcripción
de reunión + adjuntos) y un resumen de hechos del proyecto
(`project_metadata`), y cada estimación tiene en cuenta los turnos
anteriores.

| Endpoint | Qué hace |
|---|---|
| `POST /api/v1/sessions` | Crea una sesión vacía → `201 {"session_id": "<uuid4>"}` |
| `GET /api/v1/sessions/{id}` | `{"session_id", "project_metadata", "turns"}` (para el panel del cliente) |
| `DELETE /api/v1/sessions/{id}` | Borra la sesión ("Nueva conversación") → `204` |
| `POST /api/v1/sessions/{id}/estimate` | Un turno: `multipart/form-data` con `transcript` (obligatorio), `project_type` (obligatorio), `detail_level` y `output_format` (opcionales: `medium`, `phases_table`) y `attachments` (PDF/DOCX, opcional). Devuelve el mismo `EstimationResponse` de `/estimate` |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sessions
curl -X POST http://127.0.0.1:8000/api/v1/sessions/<session_id>/estimate \
  -F "transcript=Reunión de arranque del proyecto Orion..." \
  -F "project_type=web_saas" \
  -F "attachments=@requisitos.pdf"
```

**Flujo de un turno** ([`app/services/session_estimation.py`](app/services/session_estimation.py)),
dentro de un `asyncio.Lock` por sesión (dos peticiones a la misma sesión no
se intercalan):

1. Lectura y validación de los adjuntos y extracción de su texto (en un
   threadpool: es bloqueante).
2. Guardrails de entrada (moderación, injection, PII) sobre la
   transcripción y el texto de los adjuntos.
3. Mensaje de usuario: `<transcript>…</transcript>` y, por cada adjunto,
   `--- attachment: nombre ---` + `<attachment_content>…</attachment_content>`.
4. System prompt (versión `v4`, `SESSION_PROMPT_VERSION`) regenerado con el
   `project_metadata` actual.
5. `messages = [system] + historial + [mensaje actual]` → LLM (el mismo
   wrapper: timeout, reintentos, fallback y salida validada contra
   `EstimationResult`; si no valida → `502` y **el historial no cambia**) →
   guardrails de salida.
6. Se guarda el turno en el historial y se actualiza `project_metadata`.

El historial solo avanza con turnos completados. No usa el caché
semántico: con historial, dos transcripciones parecidas no son peticiones
equivalentes.

**Adjuntos: camino B, extracción local** ([`app/attachments.py`](app/attachments.py)),
con `pypdf` (PDF; licencia BSD, se descartó PyMuPDF por ser AGPL) y
`python-docx` (Word). Independiente del proveedor LLM, con control sobre el
tamaño y el contenido (se valida y se sanea antes de llegar al prompt), y
prepara el chunking de RAG de módulos posteriores. Todo en memoria, sin
escribir a disco. Validaciones:

- Número de archivos y tamaño por archivo y total, leyendo en bloques (no
  se confía en `Content-Length`) → `413`.
- Tipo real: extensión `.pdf`/`.docx` **y** firma de bytes (`%PDF-` /
  `PK\x03\x04`); no se confía en el `content_type` → `415`.
- DOCX (un ZIP): antes de abrirlo, número de entradas y tamaño
  descomprimido total (zip bombs) → `413`.
- PDF cifrado o corrupto → `422`; solo se procesan las primeras
  `MAX_PDF_PAGES` páginas.
- Texto extraído limitado por adjunto y en total, con la marca
  `[... contenido truncado ...]`.
- Nombre de archivo: solo el nombre base, sin caracteres de control y con
  longitud acotada.
- **Prompt injection**: el system prompt indica que `<transcript>`,
  `<attachment_content>` y `<project_metadata>` son datos, no
  instrucciones, y las etiquetas delimitadoras que aparezcan en el
  contenido se neutralizan (un documento no puede "cerrar" su bloque).

**`project_metadata`: extractor LLM** ([`app/metadata_extractor.py`](app/metadata_extractor.py)).
Tras cada turno, una segunda llamada al LLM (salida estructurada con
Instructor, `response_model=ProjectMetadata`, `temperature=0`,
`max_tokens` acotado) recibe el metadata actual, el último mensaje del
usuario y la última estimación, y devuelve `project_name`,
`assumed_team_size`, `mentioned_technologies` y `agreed_scope`. Se eligió
frente a regex porque es más robusto ante el lenguaje natural de una
reunión. **Coste**: una llamada extra por turno (en las pruebas, entre 1 y
2 s y unos cientos de tokens). Se fusiona con reglas (escalares solo si el
nuevo valor no es nulo; tecnologías como unión sin duplicados; alcance
reemplazado si no está vacío) y todo se valida con `ProjectMetadata`
(`extra="forbid"`, límites de longitud) antes de entrar en la sesión.
**Si el extractor falla** (timeout, proveedor caído, salida inválida), la
estimación responde igual (`200`), se registra un warning sin contenido de
la conversación y se conserva el metadata anterior.

El metadata se inyecta en el system prompt como JSON dentro de
`<project_metadata>` (vacío en el primer turno), con la instrucción de que,
si la transcripción actual lo contradice, prevalece la transcripción.

**Turno e historial efectivo.** Un turno es un par user + assistant (el
mensaje de usuario completo, con adjuntos, y la estimación en JSON). El
**historial efectivo** es el número de pares previos que se envían al LLM:
siempre `≤ MAX_TURNS` (6 por defecto). A ellos se suman el system prompt
(posición 0, regenerado en cada llamada, nunca se descarta) y el mensaje
del turno actual. Al superar el límite se descartan los pares más antiguos.
`MAX_TURNS` se configura por entorno.

**Límites configurables** (`.env`, ver `.env.example`):

| Variable | Por defecto |
|---|---|
| `MAX_TURNS` | 6 |
| `SESSION_TTL_MINUTES` (expiración por inactividad) | 60 |
| `MAX_SESSIONS` (al llegar, `POST /sessions` → `503`) | 1000 |
| `MAX_TRANSCRIPT_CHARS` | 20 000 |
| `MAX_ATTACHMENTS` | 5 |
| `MAX_ATTACHMENT_BYTES` / `MAX_TOTAL_ATTACHMENT_BYTES` | 10 MB / 25 MB |
| `MAX_PDF_PAGES` | 50 |
| `MAX_DOCX_UNCOMPRESSED_BYTES` / `MAX_DOCX_ENTRIES` | 50 MB / 1000 |
| `MAX_ATTACHMENT_CHARS` / `MAX_TOTAL_ATTACHMENT_CHARS` | 20 000 / 50 000 |
| `METADATA_EXTRACTOR_MAX_TOKENS` | 512 |

Con `MAX_SESSIONS` alcanzado se rechaza con `503` en lugar de expulsar la
sesión más antigua, que podría ser la conversación activa de otro usuario.

**Limitaciones conocidas:**

- **Memoria volátil**: las sesiones viven en un diccionario del proceso y
  se pierden al reiniciar (el cliente crea una nueva al recibir `404`).
- **Un solo worker**: con varios workers cada uno tendría su propio
  diccionario. Ejecutar uvicorn sin `--workers`. La persistencia (Redis,
  BBDD) queda para fases posteriores.
- **Sin autenticación**: el `session_id` (uuid4 aleatorio) es el único
  control de acceso a una conversación. No se registra completo en los logs
  (ni siquiera en la ruta de `http.request`).
- **Sin presupuesto de tokens**: los límites de caracteres y la ventana
  acotan el peor caso, pero no se cuentan tokens; con adjuntos grandes y 6
  turnos se puede acercar al contexto del modelo.
- **CORS**: no se configura porque el cliente Streamlit llama a la API
  desde su servidor, no desde el navegador. Si se añade un frontend web,
  hay que restringirlo a sus orígenes.
- **Rate limiting** por IP/sesión (p. ej. `slowapi`): pendiente como mejora
  futura; cada turno cuesta dos llamadas al LLM.

**Ejecución.** Igual que el resto del servicio: `uv run uvicorn app.main:app`
(o `uv run python -m uvicorn app.main:app` si App Control bloquea
`uvicorn.exe`) y `uv run streamlit run streamlit_app.py`. Tests:
`uv run pytest` (los de sesiones están en `tests/sessions/` y no usan red
ni claves: un LLM falso se inyecta con `app.dependency_overrides`).

## Wrapper de proveedores LLM (LiteLLM)

Los endpoints no hablan con OpenAI ni con Anthropic: llaman a
`LLMGateway` (`app/services/llm_gateway.py`) con el system prompt y el
mensaje de usuario, que viajan como mensajes separados (`role: "system"` y
`role: "user"`). El wrapper se encarga de todo lo relacionado con
proveedores:

- `complete(system, user)` devuelve una respuesta normalizada
  (`LLMResponse`: texto, proveedor, modelo, tokens, latencia, coste,
  `finish_reason`, `cached`).
- `complete_structured(system, user, EstimationResult)` devuelve
  directamente una instancia validada del modelo Pydantic. Es lo que usa
  `/estimate`.

### Salida estructurada (Instructor)

```python
result: EstimationResult = gateway.complete_structured(system, user, EstimationResult)
```

[Instructor](https://python.useinstructor.com/) se integra **dentro** del
wrapper (`instructor.from_litellm(..., mode=Mode.TOOLS)`), así que la
salida estructurada hereda el fallback, los reintentos, la caché y los
logs:

- **Cómo se envía el schema**: como una herramienta cuyo `parameters` es el
  JSON Schema de `EstimationResult`, con `tool_choice` forzado. LiteLLM lo
  traduce al *tool use* de Anthropic o al *function calling* de OpenAI, así
  que schemas y prompts no dependen del proveedor.
- **Reintentos de validación**: si la respuesta no valida (JSON mal formado,
  un campo fuera de rango, totales que no cuadran), Instructor reenvía al
  modelo su respuesta junto con los errores, hasta `LLM_VALIDATION_RETRIES`
  veces (2 por defecto). Si sigue sin validar, el wrapper eleva
  `InvalidStructuredOutputError` y la API responde `502` (y registra
  `llm.validation_failed` y `estimation.invalid`). Nunca llega al cliente
  una estimación a medias. No se rota de proveedor: el proveedor funciona,
  es la respuesta la que no cumple el contrato.
- **Errores del proveedor**: Instructor los envuelve en su propia
  excepción. El wrapper los desenvuelve para que sigan los reintentos de
  transporte (`LLM_MAX_RETRIES`) y el fallback de siempre.
- **Proveedor y modelo**: `LLM_PROVIDER`, `OPENAI_MODEL` y `ANTHROPIC_MODEL`.
  Cambiar de proveedor es una línea de configuración.

```
endpoint ──► LLMGateway ──► caché exact-match ──(hit)──► respuesta
                  │
                  └─(miss)─► LLM_PROVIDER ──(falla)──► otro proveedor ──(falla)──► 503
```

- **Abstracción de proveedor**: [LiteLLM](https://docs.litellm.ai/) expone
  una única API para ambos proveedores (`openai/<modelo>`,
  `anthropic/<modelo>`). Modelos por defecto: `gpt-4o-mini` y
  `claude-haiku-4-5-20251001` (snapshot fechado, para resultados
  reproducibles).
- **Fallback**: se prueba primero `LLM_PROVIDER` y, si falla, el siguiente
  proveedor con API key. Se desactiva con `LLM_FALLBACK_ENABLED=false`. En
  streaming (`LLMGateway.stream`, sin uso en los endpoints actuales) solo se
  rota **antes** del primer fragmento.
- **Reintentos**: los errores transitorios (timeout, rate limit, 5xx,
  conexión) se reintentan `LLM_MAX_RETRIES` veces sobre el mismo proveedor
  con backoff exponencial antes de rotar; los de autenticación o petición
  inválida pasan directamente al siguiente proveedor.
- **Caché exact-match**: la misma petición (system + user + parámetros +
  modelos + schema pedido) se sirve de memoria sin llamar al proveedor. En
  las llamadas estructuradas se guarda la instancia ya validada. Como el prompt
  depende de las opciones del formulario, cambiar el tipo, el nivel de
  detalle o el formato es otra entrada de caché. Clave SHA-256, TTL
  (`LLM_CACHE_TTL_SECONDS`) y tamaño máximo (`LLM_CACHE_MAX_ENTRIES`). Solo
  se cachean respuestas completas y no vacías. Es por proceso: con varios
  workers cada uno tiene la suya, y se vacía al reiniciar la API.
- **Logging estructurado** (structlog), todos los eventos con el
  `request_id` de la petición (cabecera `X-Request-ID`) y el
  `prompt_version`. `LOG_FORMAT=json` para producción.

### Qué se registra en cada llamada al LLM

| Evento | Cuándo | Campos |
|---|---|---|
| `llm.call_started` | Al inicio | `requested_model`, `target_provider`, `input_tokens_estimated`, `cache_hit` |
| `llm.provider_failed` | En cada error | `provider`, `model`, `error_category` (`timeout`, `rate_limit`, `auth`, `server_error`, `connection`, `bad_request`, `unknown`), `error_type`, `status_code`, `retry`, `will_retry`, `will_fallback`, `latency_ms` |
| `llm.fallback` | Al rotar de proveedor | `from_provider`, `to_provider`, `to_model` |
| `llm.call_completed` | Al completar | `provider`, `model`, `input_tokens`, `output_tokens`, `latency_ms` (total, incluidos reintentos), `cost_usd`, `finish_reason` (`stop` / `length` = truncada), `cache_hit`, `fallback_used`, `fallback_provider`, `attempts` |
| `llm.response_truncated` | Si `finish_reason=length` | `provider`, `model`, `output_tokens` |
| `llm.call_failed` | Si fallan todos | `providers`, `attempts`, `latency_ms` |
| `llm.validation_failed` | La salida estructurada no valida tras los reintentos | `provider`, `model`, `response_model`, `validation_attempts`, `error_type`, `errors` (solo ubicación y tipo de cada error, nunca valores) |
| `llm.stream_interrupted` | Fallo a mitad de streaming | `provider`, `error_category`, `chars_sent`, ... |

Además, `estimation.requested` registra las opciones del formulario
(`project_type`, `detail_level`, `output_format`) y el tamaño de la
descripción.

Los tokens de entrada se **estiman** al inicio con el tokenizador de
LiteLLM (aproximado para Anthropic) y el valor **real** del proveedor se
registra al completar. El coste se calcula con la tabla de precios local
de LiteLLM; una respuesta de caché cuesta `0`.

Ejemplo (`LOG_FORMAT=json`, fallback de Anthropic a OpenAI):

```json
{"event": "llm.call_started", "requested_model": "anthropic/claude-haiku-4-5", "target_provider": "anthropic", "input_tokens_estimated": 1622, "cache_hit": false}
{"event": "llm.provider_failed", "provider": "anthropic", "error_category": "auth", "status_code": 401, "retry": 0, "will_retry": false, "will_fallback": true, "latency_ms": 707.4}
{"event": "llm.fallback", "from_provider": "anthropic", "to_provider": "openai", "to_model": "openai/gpt-4o-mini"}
{"event": "llm.call_completed", "provider": "openai", "input_tokens": 1448, "output_tokens": 259, "latency_ms": 5174.9, "cost_usd": 0.000373, "finish_reason": "stop", "cache_hit": false, "fallback_used": true, "fallback_provider": "openai", "attempts": 2}
```

**Seguridad**: las API keys son `SecretStr` (nunca salen en logs ni en
`repr`) y solo existen en el contenedor de la API, no en el del cliente; no
se registra el contenido de las descripciones, solo su tamaño; los errores
del proveedor se registran pero al cliente solo le llega un mensaje
genérico (`503`/`502`); la descripción tiene un tamaño máximo (2000
caracteres); la telemetría de LiteLLM está desactivada y usa su mapa de
modelos local (sin descargas al arrancar); el contenedor Docker se ejecuta
con un usuario sin privilegios.

## Ejecución

```bash
uv run uvicorn app.main:app --reload
```

- Documentación interactiva (Swagger): http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Ejecución desde PowerShell (Windows)

1. Sitúate en la carpeta del proyecto:

   ```powershell
   cd "C:\Users\fvill\OneDrive\Documentos\formacion\aieng\proyectos\ej1-ScaffoldingFastApi"
   ```

2. (Si vienes de `git pull` o es la primera vez) instala/actualiza dependencias:

   ```powershell
   uv sync
   ```

3. Comprueba que tu `.env` tiene lo necesario: debe existir en la raíz del
   proyecto con al menos una API key (`OPENAI_API_KEY` o
   `ANTHROPIC_API_KEY`, más `ANTHROPIC_WORKSPACE_ID` si tu clave de
   Anthropic lo requiere).

4. Comprueba que el puerto 8000 está libre (por ejemplo, que no estén
   corriendo los contenedores de Docker):

   ```powershell
   Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
   ```

5. Arranca el servidor (deja esta ventana abierta, el proceso corre en primer plano):

   ```powershell
   uv run uvicorn app.main:app --reload
   ```

   Espera a ver `Uvicorn running on http://127.0.0.1:8000`.

6. Abre **otra** ventana de PowerShell y prueba el endpoint con la
   petición de ejemplo:

   ```powershell
   $body = [System.IO.File]::ReadAllBytes("$PWD\ejemplo_peticion.json")
   $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/estimate" `
     -ContentType "application/json; charset=utf-8" -Body $body

   $response.prompt_version
   $response.result.phases | Format-Table name, hours, duration_weeks, cost_eur, confidence_pct
   $response.result.summary
   ```

7. Para parar el servidor: vuelve a la ventana del paso 5 y pulsa `Ctrl+C`.

## Ejecución con Docker

Alternativa a instalar Python/`uv` en local: una única imagen (`Dockerfile`,
`python:3.12-slim` + `uv`) reutilizada por dos servicios en
`docker-compose.yml` — `api` (uvicorn, puerto 8000) y `chat` (Streamlit,
puerto 8501) — cada uno con su propio comando de arranque.

```bash
docker compose up --build -d
```

Además de `api` y `chat`, levanta `redis` (Redis Stack para el caché
semántico, sin puertos publicados). Necesita `REDIS_PASSWORD` en el `.env`
(el compose falla con un mensaje claro si falta); la API recibe
`REDIS_URL` construida a partir de ella.

- API → http://localhost:8000/docs (o `/health`)
- Cliente → http://localhost:8501

Los puertos se publican solo en `127.0.0.1` (no en `0.0.0.0`), así que no son
alcanzables desde otras máquinas de la red — el endpoint `/estimate` no tiene
autenticación propia, así que conviene mantener esta restricción salvo que se
añada una.

Ver logs / parar:

```bash
docker compose logs -f api
docker compose down
```

El servicio `chat` solo recibe `API_BASE_URL=http://api:8000` (nombre del
servicio en la red interna de Docker); no carga el `.env`, así que las API
keys de los proveedores no llegan al contenedor del cliente.

## Uso del endpoint de estimaciones

`POST /api/v1/estimate` recibe un `EstimationRequest` y devuelve un
`EstimationResponse`. [`ejemplo_peticion.json`](ejemplo_peticion.json) es
una petición válida lista para usar:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d @ejemplo_peticion.json
```

En PowerShell usa `curl.exe` (no el alias `curl`) con el mismo
`-d "@ejemplo_peticion.json"`, o el `Invoke-RestMethod` del apartado
anterior. También puedes probarlo desde `/docs` → `POST /api/v1/estimate`
→ "Try it out".

Respuesta esperada (`200`):

```json
{
  "result": {
    "summary": "Portal de reservas con SSO y panel de ocupación: unas 260 horas en 8 semanas...",
    "total_hours": 260,
    "total_duration_weeks": 8,
    "total_cost_eur": 14300,
    "confidence_pct": 75,
    "phases": [
      {
        "name": "Análisis y diseño",
        "hours": 40,
        "duration_weeks": 2,
        "cost_eur": 2200,
        "confidence_pct": 85,
        "assumptions": ["las salas y puestos ya están inventariados"]
      }
    ]
  },
  "prompt_version": "v3",
  "out_of_scope": false
}
```

(Ejemplo recortado a una fase; los totales de una respuesta real cuadran
con la suma de sus fases.)

Para usar otra versión del prompt: `POST /api/v1/estimate?prompt_version=v2`.

Códigos de respuesta (documentados en `/docs`):

| Situación | Código | Cuerpo |
|---|---|---|
| Estimación válida | 200 | `EstimationResponse` con `out_of_scope=false` |
| Fuera de alcance / confianza insuficiente | 200 | `EstimationResponse` con `out_of_scope=true` y todo a 0 |
| Petición mal formada (capa 1) | 422 | Error de validación estándar de FastAPI (`detail`), **sin eco del input**, más `request_id` |
| `prompt_version` inexistente | 422 | `{"error": {"code": "invalid_prompt_version", ...}}` |
| Descripción rechazada por un guardrail (capa 2, ENFORCE) | 400 | `{"error": {"code": "input_rejected", ...}}` genérico |
| Moderación no disponible (fail-closed) | 503 | `{"error": {"code": "moderation_unavailable", ...}}` + `Retry-After` |
| Fallan todos los proveedores LLM | 503 | `{"error": {"code": "llm_unavailable", ...}}` + `Retry-After` |
| Reintentos de Instructor agotados | 502 | `{"error": {"code": "estimation_failed", ...}}` |

Todos los errores (salvo el 422 de validación) usan el mismo schema
`ErrorResponse`:

```json
{"error": {"code": "input_rejected", "message": "No se ha podido procesar la descripción.", "request_id": "3f9c..."}}
```

> El antiguo `POST /api/v1/estimate/stream` (SSE con texto) se ha eliminado:
> con salida estructurada un JSON a medias no se puede mostrar ni validar.

## Cliente Streamlit (conversación)

[`streamlit_app.py`](streamlit_app.py) es una conversación con el servicio
(sesiones, ver "Sesión 05"):

1. Al cargar crea una sesión (`POST /api/v1/sessions`) y guarda el
   `session_id` en `st.session_state`.
2. Cada envío es un turno: transcripción (`st.text_area`), adjuntos PDF/DOCX
   (`st.file_uploader`) y los desplegables de tipo, nivel de detalle y
   formato, enviados como multipart a `POST /api/v1/sessions/{id}/estimate`
   (timeout explícito).
3. El panel lateral muestra el `project_metadata` y los turnos en historial
   (`GET /api/v1/sessions/{id}` tras cada turno). **Nueva conversación**
   borra la sesión y crea otra. Si la sesión ha expirado o el servicio se
   ha reiniciado (`404`), crea una nueva, avisa con `st.warning` y reenvía
   el turno. Los errores `413`, `415` y `422` se muestran con mensajes
   comprensibles, nunca con trazas.
4. Muestra cada turno (el último desplegado) con los totales (horas, semanas, coste y confianza) y pinta
   `result` según el formato elegido: tabla por fases, lista de partidas o
   texto narrativo ([`estimation_view.py`](estimation_view.py)). Añadir un
   formato nuevo es añadir una función ahí, sin tocar el servicio IA. Si
   `out_of_scope` es `true`, muestra un aviso con la explicación del modelo
   en lugar de la estimación.

**La salida del LLM no es de confianza.** Todo el texto que viene del
modelo (`summary`, nombres de fase, `assumptions`) se escapa antes de
meterlo en el Markdown, así que se muestra como texto plano: no puede
inyectar enlaces, imágenes, encabezados ni HTML. Cualquier otro consumidor
del endpoint (backend de negocio, otro frontend) debe hacer lo mismo:
renderizar esos campos como texto, nunca como HTML ni con
`innerHTML`/`dangerouslySetInnerHTML`.

Es un cliente HTTP puro: no importa la configuración ni las API keys del
servicio, y hereda el fallback, la caché y el logging del wrapper. Necesita
la API arrancada.

Arranque en local:

```bash
uv run streamlit run streamlit_app.py
```

> **Windows:** si tu equipo tiene activada una directiva de App Control que
> bloquea `streamlit.exe` (error `os error 4551` / "Una directiva de Control
> de aplicaciones bloqueó este archivo"), invócalo como módulo de Python en
> su lugar:
>
> ```powershell
> uv run python -m streamlit run streamlit_app.py
> ```

`API_BASE_URL` se toma de la variable de entorno, o de `st.secrets` (útil en
Streamlit Cloud); por defecto `http://localhost:8000`.

## Tests

```bash
uv run pytest
```

- `tests/prompts/test_estimation_v1.py` y `test_estimation_v2.py`: tests de los templates, sin llamar a
  ningún modelo (milisegundos). Comprueban que la descripción aparece
  literal dentro de `<project_description>`, que `output_format` es solo
  una pista (no cambia los ejemplos), que `detailed` pide las asunciones
  por fase y `summary` no, que cada ejemplo few-shot es un
  `EstimationResult` válido, y que el loader rechaza versiones
  inexistentes, variables sin definir e intentos de cerrar el bloque de la
  descripción.
- `tests/test_schemas.py`: el contrato `EstimationResult` (totales de
  horas, duración ±1 semana y coste ±5 %, coste total 0 sin
  `ZeroDivisionError`, out-of-scope coherente, confianza mínima,
  restricciones de campos y JSON Schema).
- `tests/guardrails/`: normalización y variantes de injection (mayúsculas,
  espacios, fullwidth, ancho cero), el falso positivo del chatbot, PII
  (IBAN con checksum válido e inválido, redacción), el cliente de
  moderación con el HTTP simulado (flagged, timeout, errores) y el
  registro/pipeline (modos por defecto, fail-closed/fail-open).
- `tests/test_api.py`: el endpoint devuelve un `EstimationResponse`, envía
  el schema como herramienta forzada, reenvía los errores de validación al
  modelo, responde 502 al agotar los reintentos (también con el cliente de
  Instructor mockeado), los códigos de la tabla de errores con cuerpo
  genérico (sin el patrón ni la descripción), que un guardrail en LOG_ONLY
  no altera la respuesta y que los logs no contienen la descripción ni la
  PII.
- `tests/test_llm_gateway.py` y `tests/test_llm_logging.py`: orden de
  proveedores, fallback, reintentos, caché (texto y estructurada) y qué se
  registra.
- `tests/test_estimation_view.py`: presentación en el cliente (tabla,
  lista, narrativa) y la app Streamlit renderizando una estimación.
- `tests/semantic_cache/`: el bucket (cambia con cada componente,
  determinista) y la normalización; el servicio con dobles (input
  guardrails antes del lookup, hit en active sin LLM, hit en shadow con LLM,
  no se escribe si falla un output guardrail, fail-open de Redis y de
  embeddings, entrada cacheada inválida = miss, un solo embedding por
  petición, modo off sin embedding, aislamiento por tenant y versión); los
  adaptadores (Redis inalcanzable con cooldown, embeddings con HTTP
  simulado) y la **integración con Redis Stack real** (testcontainers):
  reformulación = hit, otro formato/tenant/versión = miss, TTL aplicado,
  creación del índice idempotente.

Ningún test usa la red: los proveedores se simulan con `mock_response` /
`mock_tool_calls` de LiteLLM y la Moderation API y los embeddings con dobles
(o con el transporte HTTP simulado). No hacen falta API keys ni se consume
saldo. Los tests de integración (`-m integration`) levantan un contenedor
de Redis Stack con Docker y se saltan si Docker no está disponible; para
ejecutar solo los rápidos: `uv run pytest -m "not integration"`.

## CI

En cada `push` y `pull_request`, `.github/workflows/validate.yml` comprueba
automáticamente que la estructura de carpetas es correcta, ejecuta los
tests y verifica que el servicio arranca y `/health` responde `200`.
