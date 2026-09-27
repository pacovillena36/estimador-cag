# estimador-cag

Proyecto FastAPI con arquitectura **CAG** (Cache-Augmented Generation) para estimaciones asistidas por LLM.

## Estructura del proyecto

```
estimador-cag/
├── .github/
│   └── workflows/
│       └── validate.yml         # CI: estructura, tests y que /health responda 200
├── app/
│   ├── __init__.py
│   ├── main.py                  # App FastAPI: middleware request_id, errores, lifespan
│   ├── config.py                # Configuración (pydantic-settings, claves como SecretStr)
│   ├── logging_config.py        # Logging estructurado con structlog
│   ├── routers/
│   │   ├── __init__.py
│   │   └── estimations.py       # POST /api/v1/estimate (+ /estimate/stream, SSE)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm_gateway.py       # Wrapper de proveedores (LiteLLM): fallback, caché, logging
│   │   ├── cache.py             # Caché exact-match en memoria (TTL + LRU)
│   │   └── llm_service.py       # Construcción del prompt CAG (f-string)
│   └── context/
│       ├── __init__.py
│       └── examples.py          # Ejemplos few-shot (contexto CAG)
├── tests/                        # Tests (pytest) con proveedores simulados
├── .dockerignore
├── .env
├── .env.example
├── .gitignore
├── Dockerfile                    # Imagen única (Python 3.12 + uv) para API y chat
├── docker-compose.yml            # Servicios "api" (uvicorn) y "chat" (Streamlit)
├── pyproject.toml
├── streamlit_app.py              # Interfaz de chat (Streamlit), cliente HTTP de la API
├── transcripcion_ejemplo.md     # Transcripción de reunión de ejemplo (input de prueba)
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

## Wrapper de proveedores LLM (LiteLLM)

Los endpoints no hablan con OpenAI ni con Anthropic: llaman a
`LLMGateway` (`app/services/llm_gateway.py`), que se encarga de todo lo
relacionado con proveedores y devuelve siempre una respuesta normalizada
(`LLMResponse`: texto, proveedor, modelo, tokens, latencia, `cached`).

```
endpoint ──► LLMGateway ──► caché exact-match ──(hit)──► respuesta
                  │
                  └─(miss)─► LLM_PROVIDER ──(falla)──► otro proveedor ──(falla)──► 503
```

- **Abstracción de proveedor**: [LiteLLM](https://docs.litellm.ai/) expone
  una única API para ambos proveedores (`openai/<modelo>`,
  `anthropic/<modelo>`).
- **Fallback**: se prueba primero `LLM_PROVIDER` (con `LLM_MAX_RETRIES`
  reintentos) y, si falla (timeout, rate limit, 5xx, autenticación...), el
  siguiente proveedor con API key. Se desactiva con
  `LLM_FALLBACK_ENABLED=false`. En streaming solo se rota **antes** del
  primer fragmento: una vez enviado texto al cliente no se puede cambiar de
  proveedor sin mezclar respuestas, así que se emite un evento SSE `error`.
- **Caché exact-match**: la misma petición (system prompt + transcripción +
  parámetros + modelos) se sirve de memoria sin llamar al proveedor. Clave
  SHA-256, TTL (`LLM_CACHE_TTL_SECONDS`) y tamaño máximo
  (`LLM_CACHE_MAX_ENTRIES`). Solo se cachean respuestas completas y no
  vacías. La API recorta espacios/saltos de línea al principio y al final
  de la transcripción, así que pegar el mismo texto con o sin salto de
  línea final acierta igual; cualquier otro cambio, por pequeño que sea, es
  un miss. Es por proceso: con varios workers cada uno tiene la suya, y se
  vacía al reiniciar la API.
- **Reintentos**: los errores transitorios (timeout, rate limit, 5xx,
  conexión) se reintentan `LLM_MAX_RETRIES` veces sobre el mismo proveedor
  con backoff exponencial antes de rotar; los de autenticación o petición
  inválida pasan directamente al siguiente proveedor.
- **Logging estructurado** (structlog), todos los eventos con el
  `request_id` de la petición (cabecera `X-Request-ID`).
  `LOG_FORMAT=json` para producción.

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
`repr`); no se registra el contenido de las transcripciones, solo su
tamaño; los errores del proveedor se registran pero al cliente solo le
llega un mensaje genérico (`503`/`502`); la transcripción tiene un tamaño
máximo (`MAX_TRANSCRIPTION_CHARS`); la telemetría de LiteLLM está
desactivada y usa su mapa de modelos local (sin descargas al arrancar); el
contenedor Docker se ejecuta con un usuario sin privilegios.

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
   proyecto con al menos `LLM_PROVIDER` y la API key correspondiente
   (`ANTHROPIC_API_KEY` + `ANTHROPIC_WORKSPACE_ID` si usas Anthropic, o
   `OPENAI_API_KEY` si usas OpenAI).

4. Comprueba que el puerto 8000 está libre (por si quedó algo corriendo de antes):

   ```powershell
   Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
   ```

   Si te devuelve algo, mátalo:

   ```powershell
   Stop-Process -Id <ese_PID> -Force
   ```

5. Arranca el servidor (deja esta ventana abierta, el proceso corre en primer plano):

   ```powershell
   uv run uvicorn app.main:app --reload
   ```

   Espera a ver `Uvicorn running on http://127.0.0.1:8000`.

6. Abre **otra** ventana de PowerShell y comprueba `/health`:

   ```powershell
   Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
   ```

   Debe devolver `status: ok`.

7. Prueba el endpoint de estimación con la transcripción de ejemplo:

   ```powershell
   $path = Join-Path (Get-Location) "transcripcion_ejemplo.md"
   $transcription = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
   $bodyJson = @{ transcription = $transcription } | ConvertTo-Json -Compress
   $bytes = [System.Text.Encoding]::UTF8.GetBytes($bodyJson)

   $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/estimate" `
     -ContentType "application/json; charset=utf-8" `
     -Body $bytes

   $response.provider
   $response.model
   $response.estimation
   ```

   Usa `[System.IO.File]::ReadAllText` en vez de `Get-Content -Raw` — en
   PowerShell 5.1, `Get-Content` añade metadatos del proveedor de archivos
   que rompen el JSON al serializarlo con `ConvertTo-Json`.

8. (Opcional) Prueba también desde el navegador: abre `http://127.0.0.1:8000/docs`,
   expande `POST /api/v1/estimate` → "Try it out" → pega un JSON con tu
   propia transcripción → "Execute".

9. Para parar el servidor: vuelve a la ventana del paso 5 y pulsa `Ctrl+C`.

## Ejecución con Docker

Alternativa a instalar Python/`uv` en local: una única imagen (`Dockerfile`,
`python:3.12-slim` + `uv`) reutilizada por dos servicios en
`docker-compose.yml` — `api` (uvicorn, puerto 8000) y `chat` (Streamlit,
puerto 8501) — cada uno con su propio comando de arranque.

```bash
docker compose up --build -d
```

- API → http://localhost:8000/docs (o `/health`)
- Chat → http://localhost:8501

Los puertos se publican solo en `127.0.0.1` (no en `0.0.0.0`), así que no son
alcanzables desde otras máquinas de la red — el endpoint `/estimate` no tiene
autenticación propia, así que conviene mantener esta restricción salvo que se
añada una.

Ver logs / parar:

```bash
docker compose logs -f
docker compose down
```

El servicio `chat` recibe `API_BASE_URL=http://api:8000` (nombre del
servicio en la red interna de Docker) para poder llamar a la API — ver
[Interfaz conversacional (Streamlit)](#interfaz-conversacional-streamlit).

## Uso del endpoint de estimaciones

`POST /api/v1/estimate` recibe la transcripción de una reunión y devuelve
una estimación generada por el LLM configurado, usando como referencia los
ejemplos few-shot de `app/context/examples.py` (arquitectura CAG).

En [`transcripcion_ejemplo.md`](transcripcion_ejemplo.md) tienes una
transcripción de reunión de ejemplo lista para usar como parámetro de
prueba.

**Desde Bash / Git Bash**, usando Python para generar el JSON con el texto ya escapado:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d "$(python -c "import json,sys; print(json.dumps({'transcription': open('transcripcion_ejemplo.md', encoding='utf-8').read()}))")"
```

**Desde PowerShell**, escribiendo el body a un archivo temporal para que `curl.exe`
lo lea directamente con `-d "@archivo"` (evita que PowerShell trocee el texto
multilínea al pasarlo como argumento):

```powershell
$projectDir = "C:\Users\fvill\OneDrive\Documentos\formacion\aieng\proyectos\ej1-ScaffoldingFastApi"
$path = Join-Path $projectDir "transcripcion_ejemplo.md"
$transcription = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
$bodyJson = @{ transcription = $transcription } | ConvertTo-Json -Compress

$bodyFile = Join-Path $env:TEMP "body_temp.json"
[System.IO.File]::WriteAllText($bodyFile, $bodyJson, [System.Text.Encoding]::UTF8)

curl.exe -X POST http://127.0.0.1:8000/api/v1/estimate `
  -H "Content-Type: application/json; charset=utf-8" `
  -d "@$bodyFile"

Remove-Item $bodyFile
```

La clave es `-d "@$bodyFile"`: el `@` le dice a `curl.exe` que lea el body
directamente del archivo, así nunca pasa por el troceo de argumentos de
PowerShell.

O simplemente abre `/docs`, pega el contenido de `transcripcion_ejemplo.md`
en el campo `transcription` de `POST /api/v1/estimate` y ejecuta la
petición desde ahí.

Respuesta esperada (`200`):

```json
{
  "estimation": "## Estimación: ...",
  "model": "claude-haiku-4-5",
  "provider": "anthropic",
  "cached": false,
  "cost_usd": 0.001834,
  "finish_reason": "stop"
}
```

`provider` y `model` indican quién respondió realmente (puede ser el
proveedor de fallback). Si todos los proveedores fallan la API devuelve
`503` con cabecera `Retry-After`.

## Interfaz conversacional (Streamlit)

Además de la API, el proyecto incluye una interfaz de chat en
[`streamlit_app.py`](streamlit_app.py). Es un cliente HTTP de la API
(`POST /api/v1/estimate/stream`, en `API_BASE_URL`), así que hereda el
fallback, la caché y el logging del wrapper sin duplicar lógica; necesita
la API arrancada.

Arranque:

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

Abre `http://localhost:8501`, pega la transcripción (o resumen) de una
reunión en el cuadro de chat y la estimación se genera en la misma sesión.

**Características:**

- **Chat con historial de sesión**: cada transcripción enviada y su
  estimación se guardan en `st.session_state`, así que el historial persiste
  mientras dure la sesión del navegador (se pierde al recargar la página).
- **Streaming token a token**: la respuesta se muestra progresivamente según
  la va generando el modelo (`st.write_stream`), usando el modo streaming
  nativo del proveedor a través de LiteLLM (`stream=True`) — no es una
  simulación sobre una respuesta ya completa. Las respuestas cacheadas se
  muestran de golpe.
- **Panel lateral (sidebar)** con:
  - El **system prompt activo** en modo solo lectura.
  - Los **ejemplos de contexto CAG** (`ESTIMATION_EXAMPLES`) tal como se
    inyectan en el prompt.
  - **Métricas de la última llamada**: proveedor y modelo que respondieron,
    tokens de entrada/salida, tiempo de respuesta y si vino de caché.
- **API keys**: se leen igual que en la API, vía `app.config.settings`
  (`.env`). Para desplegar en Streamlit Cloud (donde no existe `.env`), si
  una clave no está en el entorno pero sí en `st.secrets`, se copia a las
  variables de entorno antes de inicializar la configuración — nunca se
  hardcodea ninguna clave en el código.

## Tests

```bash
uv run pytest
```

Cubren el orden de proveedores, el fallback (también en streaming), la
caché exact-match, los errores genéricos de la API y el `X-Request-ID`.
Los proveedores se simulan con `mock_response` de LiteLLM: no hacen falta
API keys ni se consume saldo.

## CI

En cada `push` y `pull_request`, `.github/workflows/validate.yml` comprueba
automáticamente que la estructura de carpetas es correcta, ejecuta los
tests y verifica que el servicio arranca y `/health` responde `200`.
