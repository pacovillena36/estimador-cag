# estimador-cag

Proyecto FastAPI con arquitectura **CAG** (Cache-Augmented Generation) para estimaciones asistidas por LLM.

## Estructura del proyecto

```
estimador-cag/
├── .github/
│   └── workflows/
│       └── validate.yml         # CI: valida estructura y que /health responda 200
├── app/
│   ├── __init__.py
│   ├── main.py                  # Punto de entrada de la app FastAPI
│   ├── config.py                # Configuración (pydantic-settings)
│   ├── routers/
│   │   ├── __init__.py
│   │   └── estimations.py       # POST /api/v1/estimate
│   ├── services/
│   │   ├── __init__.py
│   │   └── llm_service.py       # Construcción del prompt CAG + llamada al LLM
│   └── context/
│       ├── __init__.py
│       └── examples.py          # Ejemplos few-shot (contexto CAG)
├── .env
├── .env.example
├── .gitignore
├── pyproject.toml
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

## Ejecución

```bash
uv run uvicorn app.main:app --reload
```

- Documentación interactiva (Swagger): http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Uso del endpoint de estimaciones

`POST /api/v1/estimate` recibe la transcripción de una reunión y devuelve
una estimación generada por el LLM configurado, usando como referencia los
ejemplos few-shot de `app/context/examples.py` (arquitectura CAG).

En [`transcripcion_ejemplo.md`](transcripcion_ejemplo.md) tienes una
transcripción de reunión de ejemplo lista para usar como parámetro de
prueba. Por ejemplo, con `jq` para extraer el texto y montarlo en el JSON:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d "$(python -c "import json,sys; print(json.dumps({'transcription': open('transcripcion_ejemplo.md', encoding='utf-8').read()}))")"
```

O simplemente abre `/docs`, pega el contenido de `transcripcion_ejemplo.md`
en el campo `transcription` de `POST /api/v1/estimate` y ejecuta la
petición desde ahí.

Respuesta esperada (`200`):

```json
{
  "estimation": "## Estimación: ...",
  "model": "claude-haiku-4-5",
  "provider": "anthropic"
}
```

## CI

En cada `push` y `pull_request`, `.github/workflows/validate.yml` comprueba
automáticamente que la estructura de carpetas es correcta y que el servicio
arranca y `/health` responde `200`.
