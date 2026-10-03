"""Cliente Streamlit del estimador: formulario que construye un
EstimationRequest y lo envía al servicio IA (POST /api/v1/estimate).

Es un cliente HTTP puro: no importa la configuración ni las API keys del
servicio. Solo reutiliza el contrato (app/schemas.py) para validar el
formulario antes de enviarlo, con las mismas reglas que aplica la API.

El servicio devuelve la estimación estructurada (EstimationResult); la
presentación según el formato elegido se hace aquí (estimation_view.py).
"""

import os

import httpx2
import streamlit as st
from pydantic import ValidationError

from app.schemas import (
    DetailLevel,
    EstimationRequest,
    EstimationResponse,
    OutputFormat,
    ProjectType,
)
from estimation_view import render


def _api_base_url() -> str:
    """URL base del servicio IA. En Docker Compose llega por variable de
    entorno (http://api:8000); en Streamlit Cloud puede venir de st.secrets;
    en local, por defecto, uvicorn en localhost."""
    if url := os.environ.get("API_BASE_URL"):
        return url
    try:
        return st.secrets["API_BASE_URL"]
    except Exception:  # no hay secrets.toml
        return "http://localhost:8000"


API_BASE_URL = _api_base_url()

PROJECT_TYPE_LABELS = {
    ProjectType.MOBILE_APP: "📱 App móvil",
    ProjectType.WEB_SAAS: "🌐 Web SaaS",
    ProjectType.INTERNAL_TOOL: "🛠️ Herramienta interna",
    ProjectType.DATA_PIPELINE: "🔀 Pipeline de datos",
}
DETAIL_LEVEL_LABELS = {
    DetailLevel.SUMMARY: "Resumen",
    DetailLevel.MEDIUM: "Medio",
    DetailLevel.DETAILED: "Detallado",
}
OUTPUT_FORMAT_LABELS = {
    OutputFormat.PHASES_TABLE: "Tabla por fases",
    OutputFormat.LINE_ITEMS: "Lista de partidas",
    OutputFormat.NARRATIVE: "Texto narrativo",
}

_FIELD_LABELS = {
    "description": "Descripción",
    "project_type": "Tipo de proyecto",
    "detail_level": "Nivel de detalle",
    "output_format": "Formato de salida",
}


class EstimationAPIError(Exception):
    """Error devuelto por el servicio IA, con un mensaje apto para el usuario."""


def _validation_messages(exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors():
        field = _FIELD_LABELS.get(str(error["loc"][0]), str(error["loc"][0]))
        ctx = error.get("ctx", {})
        if error["type"] == "string_too_short":
            messages.append(f"{field}: escribe al menos {ctx['min_length']} caracteres.")
        elif error["type"] == "string_too_long":
            messages.append(f"{field}: máximo {ctx['max_length']} caracteres.")
        else:
            messages.append(f"{field}: {error['msg']}")
    return messages


def request_estimation(request: EstimationRequest) -> EstimationResponse:
    """POST /api/v1/estimate con el EstimationRequest serializado a JSON."""
    try:
        response = httpx2.post(
            f"{API_BASE_URL}/api/v1/estimate",
            json=request.model_dump(mode="json"),
            timeout=120.0,
        )
    except httpx2.HTTPError as exc:
        raise EstimationAPIError(
            f"No se pudo conectar con el servicio IA en {API_BASE_URL}."
        ) from exc

    if response.status_code >= 400:
        # La API devuelve un "detail" pensado para el usuario (sin detalles
        # internos del proveedor); en los 422 es una lista de errores.
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, list):
            detail = "; ".join(item.get("msg", "") for item in detail)
        raise EstimationAPIError(detail or f"El servicio respondió {response.status_code}.")

    return EstimationResponse.model_validate(response.json())


st.set_page_config(page_title="Estimador de proyectos", page_icon="🧮")

st.title("🧮 Estimador de proyectos")
st.write(
    "Describe el proyecto, elige el tipo, el nivel de detalle y el formato, "
    "y recibirás una estimación por fases: horas, semanas, coste y confianza."
)

with st.form("estimation_form"):
    description = st.text_area(
        "Descripción del proyecto",
        height=180,
        max_chars=2000,
        placeholder=(
            "Ej.: Portal interno para que los empleados reserven salas y "
            "puestos de trabajo, con login corporativo y un panel de ocupación."
        ),
    )
    col_type, col_detail, col_format = st.columns(3)
    project_type = col_type.selectbox(
        "Tipo de proyecto",
        options=list(ProjectType),
        format_func=PROJECT_TYPE_LABELS.get,
    )
    detail_level = col_detail.selectbox(
        "Nivel de detalle",
        options=list(DetailLevel),
        index=list(DetailLevel).index(DetailLevel.MEDIUM),
        format_func=DETAIL_LEVEL_LABELS.get,
    )
    output_format = col_format.selectbox(
        "Formato de salida",
        options=list(OutputFormat),
        format_func=OUTPUT_FORMAT_LABELS.get,
    )
    submitted = st.form_submit_button("Enviar", type="primary")

if submitted:
    try:
        estimation_request = EstimationRequest(
            description=description,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
        )
    except ValidationError as exc:
        for message in _validation_messages(exc):
            st.error(message)
    else:
        with st.spinner("Generando la estimación..."):
            try:
                estimation = request_estimation(estimation_request)
            except EstimationAPIError as exc:
                st.error(f"⚠️ {exc}")
            else:
                # Se guarda en la sesión para que la última estimación siga
                # visible en los reruns de Streamlit.
                st.session_state.last_estimation = {
                    "request": estimation_request,
                    "response": estimation,
                }

if last := st.session_state.get("last_estimation"):
    request, response = last["request"], last["response"]
    st.divider()
    st.subheader("Estimación")
    st.caption(
        f"{PROJECT_TYPE_LABELS[request.project_type]} · "
        f"{DETAIL_LEVEL_LABELS[request.detail_level]} · "
        f"{OUTPUT_FORMAT_LABELS[request.output_format]} · "
        f"prompt {response.prompt_version}"
    )
    result = response.result
    hours, weeks, cost, confidence = st.columns(4)
    hours.metric("Horas", result.total_hours)
    weeks.metric("Semanas", result.total_duration_weeks)
    cost.metric("Coste", f"{result.total_cost_eur:,} €".replace(",", "."))
    confidence.metric("Confianza", f"{result.confidence_pct} %")
    st.markdown(render(result, request.output_format, request.detail_level))
