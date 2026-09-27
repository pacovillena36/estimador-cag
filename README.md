# estimador-cag

Servicio IA en FastAPI que genera estimaciones de esfuerzo de proyectos de
software a partir de su descripción, con un cliente web en Streamlit. Los
prompts son templates Jinja2 versionados con ejemplos few-shot, y las
llamadas al LLM pasan por un wrapper de proveedores (OpenAI / Anthropic)
con fallback, caché y logging estructurado.

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
│   ├── schemas.py                # Contrato cliente-servicio (Pydantic v2)
│   ├── logging_config.py         # Logging estructurado con structlog
│   ├── prompts/
│   │   ├── loader.py             # render_estimation_prompt(request, version) -> (system, user)
│   │   └── estimation/
│   │       └── v1/
│   │           ├── system.j2     # Rol, instrucciones, formato y nivel de detalle
│   │           ├── user.j2       # Envuelve la descripción en <project_description>
│   │           └── examples.j2   # Ejemplos few-shot (se renderizan en el formato pedido)
│   ├── routers/
│   │   └── estimations.py        # POST /api/v1/estimate (+ /estimate/stream, SSE)
│   └── services/
│       ├── llm_gateway.py        # Wrapper de proveedores (LiteLLM): fallback, caché, logging
│       └── cache.py              # Caché exact-match en memoria (TTL + LRU)
├── tests/
│   ├── prompts/
│   │   └── test_estimation_v1.py # Tests del template (sin llamar a ningún modelo)
│   ├── test_api.py
│   ├── test_llm_gateway.py
│   └── test_llm_logging.py
├── .env.example
├── Dockerfile                     # Imagen única (Python 3.12 + uv) para API y cliente
├── docker-compose.yml             # Servicios "api" (uvicorn) y "chat" (Streamlit)
├── ejemplo_peticion.json          # Petición de ejemplo para probar el endpoint
├── pyproject.toml
├── streamlit_app.py               # Cliente Streamlit (formulario), cliente HTTP de la API
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

## Contrato cliente-servicio

Definido con Pydantic v2 en [`app/schemas.py`](app/schemas.py) y reutilizado
por el cliente Streamlit para validar el formulario antes de enviarlo.

**`EstimationRequest`**

| Campo | Tipo | Valores |
|---|---|---|
| `description` | `str` | 20 a 2000 caracteres (se recortan espacios al inicio/fin) |
| `project_type` | `ProjectType` | `mobile_app`, `web_saas`, `internal_tool`, `data_pipeline` |
| `detail_level` | `DetailLevel` | `summary`, `medium`, `detailed` |
| `output_format` | `OutputFormat` | `phases_table`, `line_items`, `narrative` |

**`EstimationResponse`**: `text` (la estimación, texto libre) y
`prompt_version` (versión del prompt usada, p. ej. `v1`).

## Prompts versionados (Jinja2)

Los prompts viven en `app/prompts/<nombre>/<versión>/` y se renderizan con
[`app/prompts/loader.py`](app/prompts/loader.py):

```python
system, user = render_estimation_prompt(request, version="v1")
```

- **`system.j2`**: rol del modelo, instrucciones generales (con una pauta
  específica por `project_type`), un bloque condicional por `output_format`
  (cómo formatear la salida) y otro por `detail_level` (qué nivel de
  detalle dar). Incluye `examples.j2` con `{% include %}`.
- **`user.j2`**: envuelve la descripción del proyecto en
  `<project_description>...</project_description>`.
- **`examples.j2`**: dos ejemplos few-shot (una app móvil y un pipeline de
  datos). Sus datos se definen una sola vez y se renderizan en el mismo
  formato y nivel de detalle que se piden al modelo, para que los ejemplos
  nunca contradigan las instrucciones.

El entorno de Jinja2 usa `StrictUndefined` (una variable que falte es un
error, no un hueco vacío en el prompt), `trim_blocks` y `lstrip_blocks`.

**Nueva versión**: copia `estimation/v1/` a `estimation/v2/`, edítala y pon
`PROMPT_VERSION=v2` en `.env`. El resto del código no cambia, y la API
comprueba al arrancar que la versión configurada existe.

**Seguridad frente a prompt injection**: la descripción va siempre en el
mensaje `user` (nunca en el `system`), delimitada por etiquetas, y el system
prompt indica tratarla solo como datos. Si el usuario escribe las propias
etiquetas `<project_description>` para "cerrar" el bloque, el loader las
neutraliza.

## Wrapper de proveedores LLM (LiteLLM)

Los endpoints no hablan con OpenAI ni con Anthropic: llaman a
`LLMGateway` (`app/services/llm_gateway.py`) con el system prompt y el
mensaje de usuario, que viajan como mensajes separados (`role: "system"` y
`role: "user"`). El wrapper se encarga de todo lo relacionado con
proveedores y devuelve siempre una respuesta normalizada (`LLMResponse`:
texto, proveedor, modelo, tokens, latencia, coste, `finish_reason`, `cached`).

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
  streaming solo se rota **antes** del primer fragmento: una vez enviado
  texto al cliente no se puede cambiar de proveedor sin mezclar respuestas,
  así que se emite un evento SSE `error`.
- **Reintentos**: los errores transitorios (timeout, rate limit, 5xx,
  conexión) se reintentan `LLM_MAX_RETRIES` veces sobre el mismo proveedor
  con backoff exponencial antes de rotar; los de autenticación o petición
  inválida pasan directamente al siguiente proveedor.
- **Caché exact-match**: la misma petición (system + user + parámetros +
  modelos) se sirve de memoria sin llamar al proveedor. Como el prompt
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
   $response.text
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
  "text": "| Fase | Tareas | Horas | confidence_pct |\n|---|---|---|---|\n...",
  "prompt_version": "v1"
}
```

Errores: `422` si la petición no cumple el contrato (descripción de menos
de 20 o más de 2000 caracteres, o un valor fuera de los enums), `503` con
cabecera `Retry-After` si fallan todos los proveedores y `502` si el modelo
devuelve una respuesta vacía.

`POST /api/v1/estimate/stream` acepta el mismo body y devuelve la
estimación en streaming (SSE): eventos `delta` con el texto, y al final un
evento `done` con `prompt_version`, proveedor, modelo, tokens, latencia,
coste, `finish_reason` y `cached` (o `error` si el proveedor se interrumpe).

## Cliente Streamlit (formulario)

[`streamlit_app.py`](streamlit_app.py) es un formulario (`st.form`) con la
descripción del proyecto y tres desplegables (tipo de proyecto, nivel de
detalle y formato de salida). Al pulsar **Enviar**:

1. Construye un `EstimationRequest` con las clases de `app/schemas.py`, así
   que los errores (p. ej. descripción demasiado corta) se muestran sin
   llegar a llamar a la API.
2. Hace `POST /api/v1/estimate` con ese JSON al servicio IA en
   `API_BASE_URL`.
3. Muestra el texto de la respuesta (renderizado como Markdown) junto con
   las opciones elegidas y la versión del prompt.

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

- `tests/prompts/test_estimation_v1.py`: tests del template, sin llamar a
  ningún modelo (milisegundos). Comprueban que la descripción aparece
  literal dentro de `<project_description>`, que `phases_table` pide
  `confidence_pct` y `narrative` no, que `detailed` pide las asunciones por
  fase y `summary` no, y que el loader rechaza versiones inexistentes,
  variables sin definir e intentos de cerrar el bloque de la descripción.
- `tests/test_api.py`: contrato del endpoint, mensajes `system`/`user`
  separados, validación (422) y errores genéricos.
- `tests/test_llm_gateway.py` y `tests/test_llm_logging.py`: orden de
  proveedores, fallback, reintentos, caché y qué se registra.

Los proveedores se simulan con `mock_response` de LiteLLM: no hacen falta
API keys ni se consume saldo.

## CI

En cada `push` y `pull_request`, `.github/workflows/validate.yml` comprueba
automáticamente que la estructura de carpetas es correcta, ejecuta los
tests y verifica que el servicio arranca y `/health` responde `200`.
