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
or
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
 La clave es -d "@$bodyFile" — el @ le dice a curl.exe que lea el body directamente del archivo, así nunca pasa por el troceo de PowerShell (que es lo que rompía todo antes con el texto multilínea pasado como
  argumento).
  
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
