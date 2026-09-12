# estimador-cag

Proyecto FastAPI con arquitectura **CAG** (Cache-Augmented Generation) para estimaciones asistidas por LLM.

## Estructura del proyecto

```
estimador-cag/
├── app/
│   ├── __init__.py
│   ├── main.py                  # Punto de entrada de la app FastAPI
│   ├── config.py                # Configuración (pydantic-settings)
│   ├── routers/
│   │   ├── __init__.py
│   │   └── estimations.py       # Endpoints de estimaciones
│   ├── services/
│   │   ├── __init__.py
│   │   └── llm_service.py       # Cliente(s) LLM (OpenAI / Anthropic)
│   └── context/
│       ├── __init__.py
│       └── examples.py          # Ejemplos / contexto cacheado (CAG)
├── .env
├── .env.example
├── .gitignore
├── pyproject.toml
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

## Estado actual

Este repositorio contiene únicamente el esqueleto del proyecto (estructura de carpetas y dependencias). La lógica de negocio se implementará en pasos posteriores.
