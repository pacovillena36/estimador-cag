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
│   ├── prompts/
│   │   ├── loader.py             # render_estimation_prompt(request, version) -> (system, user)
│   │   └── estimation/
│   │       ├── v1/
│   │       │   ├── system.j2     # Rol, criterio de estimación y pistas de contenido
│   │       │   ├── user.j2       # Envuelve la descripción en <project_description>
│   │       │   └── examples.j2   # Ejemplos few-shot (instancias de EstimationResult en JSON)
│   │       └── v2/               # Igual que v1 salvo el set de ejemplos
│   ├── routers/
│   │   └── estimations.py        # POST /api/v1/estimate
│   └── services/
│       ├── llm_gateway.py        # Wrapper de proveedores (LiteLLM + Instructor): fallback, caché, logging
│       └── cache.py              # Caché exact-match en memoria (TTL + LRU)
├── tests/
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
├── streamlit_app.py               # Cliente Streamlit (formulario), cliente HTTP de la API
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

**`EstimationResponse`**: `result` (un `EstimationResult`) y
`prompt_version` (versión del prompt usada, p. ej. `v1`).

**`EstimationResult`**: la estimación estructurada. Es a la vez el contrato
con el LLM (Instructor envía su JSON Schema al proveedor), la documentación
OpenAPI del endpoint (`/docs`) y el tipo que circula por el código: el
shape no se repite a mano en ningún otro sitio, tampoco en los prompts.

| Campo | Tipo | Restricciones |
|---|---|---|
| `summary` | `str` | |
| `total_hours` | `int` | ≥ 1; igual a la suma de `hours` de las fases |
| `total_duration_weeks` | `int` | ≥ 1; suma de `duration_weeks` de las fases (±1 semana) |
| `total_cost_eur` | `int` | ≥ 0; suma de `cost_eur` de las fases (±5 %) |
| `confidence_pct` | `int` | 0 a 100 |
| `phases` | `list[Phase]` | al menos una |

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
  "prompt_version": "v1"
}
```

(Ejemplo recortado a una fase; los totales de una respuesta real cuadran
con la suma de sus fases.)

Para usar otra versión del prompt: `POST /api/v1/estimate?prompt_version=v2`.

Errores: `422` si la petición no cumple el contrato (descripción de menos
de 20 o más de 2000 caracteres, un valor fuera de los enums o una
`prompt_version` que no existe), `503` con
cabecera `Retry-After` si fallan todos los proveedores y `502` si la
respuesta del modelo no cumple `EstimationResult` ni tras los reintentos de
validación.

> El antiguo `POST /api/v1/estimate/stream` (SSE con texto) se ha eliminado:
> con salida estructurada un JSON a medias no se puede mostrar ni validar.

## Cliente Streamlit (formulario)

[`streamlit_app.py`](streamlit_app.py) es un formulario (`st.form`) con la
descripción del proyecto y tres desplegables (tipo de proyecto, nivel de
detalle y formato de salida). Al pulsar **Enviar**:

1. Construye un `EstimationRequest` con las clases de `app/schemas.py`, así
   que los errores (p. ej. descripción demasiado corta) se muestran sin
   llegar a llamar a la API.
2. Hace `POST /api/v1/estimate` con ese JSON al servicio IA en
   `API_BASE_URL`.
3. Muestra los totales (horas, semanas, coste y confianza) y pinta
   `result` según el formato elegido: tabla por fases, lista de partidas o
   texto narrativo ([`estimation_view.py`](estimation_view.py)). Añadir un
   formato nuevo es añadir una función ahí, sin tocar el servicio IA.

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
  `ZeroDivisionError`, restricciones de campos y JSON Schema).
- `tests/test_api.py`: el endpoint devuelve un `EstimationResponse`, envía
  el schema como herramienta forzada, reenvía los errores de validación al
  modelo, responde 502 al agotar los reintentos (también con el cliente de
  Instructor mockeado) y mantiene la validación (422) y el 503.
- `tests/test_llm_gateway.py` y `tests/test_llm_logging.py`: orden de
  proveedores, fallback, reintentos, caché (texto y estructurada) y qué se
  registra.
- `tests/test_estimation_view.py`: presentación en el cliente (tabla,
  lista, narrativa) y la app Streamlit renderizando una estimación.

Los proveedores se simulan con `mock_response` / `mock_tool_calls` de
LiteLLM: no hacen falta API keys ni se consume saldo.

## CI

En cada `push` y `pull_request`, `.github/workflows/validate.yml` comprueba
automáticamente que la estructura de carpetas es correcta, ejecuta los
tests y verifica que el servicio arranca y `/health` responde `200`.
