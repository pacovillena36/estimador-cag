# Imagen base con Python 3.12 (misma versión que .python-version)
FROM python:3.12-slim

# uv como binario standalone, sin instalar Python adicional
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Instala dependencias primero para aprovechar la cache de capas de Docker:
# solo se reinstalan si cambian pyproject.toml/uv.lock, no en cada cambio de código.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copia el resto del código y sincroniza el proyecto en sí
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000 8501
