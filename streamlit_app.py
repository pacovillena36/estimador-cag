"""Cliente Streamlit del estimador: conversación con memoria (sesiones).

Flujo:
- Al cargar, si no hay session_id en st.session_state, crea una sesión
  (POST /api/v1/sessions).
- Cada envío es un turno: transcripción + adjuntos PDF/DOCX opcionales en
  multipart a POST /api/v1/sessions/{id}/estimate. El servicio recuerda los
  turnos anteriores (ventana deslizante) y el project_metadata.
- Tras cada turno, el panel lateral muestra el project_metadata y el número
  de turnos en historial (GET /api/v1/sessions/{id}).
- "Nueva conversación" borra la sesión anterior y crea otra.
- Si el servicio responde 404 (sesión expirada o servicio reiniciado), se
  crea una sesión nueva automáticamente, se avisa y se reenvía el turno.

Es un cliente HTTP puro: no importa la configuración ni las API keys del
servicio. La URL sale de API_BASE_URL. La presentación de cada estimación se
hace en estimation_view.py (el texto del modelo se muestra como texto plano).
"""

import os
from dataclasses import dataclass

import httpx2
import streamlit as st

from app.schemas import DetailLevel, EstimationResponse, OutputFormat, ProjectType
from app.sessions import SessionState
from estimation_view import plain, render

TIMEOUT_SECONDS = 180.0  # un turno hace dos llamadas al LLM (estimación + metadata)
MAX_TRANSCRIPT_CHARS = 20_000  # mismo valor por defecto que MAX_TRANSCRIPT_CHARS en el servicio


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
SESSIONS_URL = f"{API_BASE_URL}/api/v1/sessions"

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


class EstimationAPIError(Exception):
    """Error devuelto por el servicio IA, con un mensaje apto para el usuario."""


class SessionExpiredError(EstimationAPIError):
    """404: la sesión no existe (expirada o servicio reiniciado)."""


@dataclass(frozen=True)
class Turn:
    transcript: str
    attachment_names: list[str]
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
    response: EstimationResponse


# ------------------------------------------------------------- cliente HTTP


def _error_message(response: httpx2.Response) -> str:
    """Mensaje comprensible para el usuario; nunca vuelca trazas. La API
    devuelve {"error": {"code", "message", "request_id"}} con un mensaje
    genérico, y en los 422 de formulario {"detail": [...]}."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(error := body.get("error"), dict) and error.get("message"):
        message = error["message"]
        if error.get("request_id"):
            message += f" (referencia: {error['request_id']})"
        return message
    detail = body.get("detail")
    if isinstance(detail, list):
        for item in detail:
            if item.get("loc", [None])[-1] == "transcript":
                if item.get("type") == "string_too_long":
                    return f"La transcripción supera el máximo de {item.get('ctx', {}).get('max_length')} caracteres."
                return "La transcripción no puede estar vacía."
        return "Revisa los datos del formulario."
    if response.status_code == 413:
        return "Los adjuntos superan el tamaño o el número máximo permitido."
    return f"El servicio respondió {response.status_code}."


def _request(method: str, url: str, **kwargs) -> httpx2.Response:
    try:
        response = httpx2.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
    except httpx2.HTTPError as exc:
        raise EstimationAPIError(f"No se pudo conectar con el servicio IA en {API_BASE_URL}.") from exc
    if response.status_code == 404:
        raise SessionExpiredError(_error_message(response))
    if response.status_code >= 400:
        raise EstimationAPIError(_error_message(response))
    return response


def create_session() -> str:
    return _request("POST", SESSIONS_URL).json()["session_id"]


def fetch_session(session_id: str) -> SessionState:
    return SessionState.model_validate(_request("GET", f"{SESSIONS_URL}/{session_id}").json())


def delete_session(session_id: str) -> None:
    try:
        _request("DELETE", f"{SESSIONS_URL}/{session_id}")
    except EstimationAPIError:
        pass  # si ya no existe o el servicio no responde, no pasa nada


def send_turn(session_id: str, transcript: str, files, *, project_type, detail_level, output_format) -> EstimationResponse:
    data = {
        "transcript": transcript,
        "project_type": project_type.value,
        "detail_level": detail_level.value,
        "output_format": output_format.value,
    }
    multipart = [("attachments", (f.name, f.getvalue(), f.type or "application/octet-stream")) for f in files]
    response = _request("POST", f"{SESSIONS_URL}/{session_id}/estimate", data=data, files=multipart or None)
    return EstimationResponse.model_validate(response.json())


# ------------------------------------------------------------ estado local


def start_new_conversation() -> None:
    """Borra la sesión anterior (si hay) y crea otra; resetea el estado."""
    if previous := st.session_state.get("session_id"):
        delete_session(previous)
    st.session_state.session_id = create_session()
    st.session_state.turns = []
    st.session_state.session_info = None
    st.session_state.uploader_key = st.session_state.get("uploader_key", 0) + 1


def ensure_session() -> None:
    if st.session_state.get("session_id"):
        return
    try:
        start_new_conversation()
    except EstimationAPIError as exc:
        st.error(f"⚠️ {exc}")
        st.stop()


# ------------------------------------------------------------------- vista


def render_sidebar() -> None:
    info: SessionState | None = st.session_state.get("session_info")
    with st.sidebar:
        st.header("Conversación")
        st.caption(f"Sesión {str(st.session_state.session_id)[:8]}…")
        st.metric("Turnos en historial", info.turns if info else 0)
        st.subheader("Proyecto (project_metadata)")
        metadata = info.project_metadata if info else None
        if metadata is None or metadata.is_empty():
            st.caption("Aún no hay datos: se completan a medida que avanza la conversación.")
        else:
            st.markdown(f"**Nombre:** {plain(metadata.project_name or '—')}")
            st.markdown(f"**Equipo asumido:** {metadata.assumed_team_size or '—'}")
            technologies = ", ".join(plain(t) for t in metadata.mentioned_technologies) or "—"
            st.markdown(f"**Tecnologías:** {technologies}")
            st.markdown(f"**Alcance acordado:** {plain(metadata.agreed_scope or '—')}")
        if st.button("🆕 Nueva conversación", use_container_width=True):
            try:
                start_new_conversation()
            except EstimationAPIError as exc:
                st.error(f"⚠️ {exc}")
            st.rerun()


def render_turn(index: int, turn: Turn, *, expanded: bool) -> None:
    response = turn.response
    label = f"Turno {index} · " + plain(turn.transcript[:60]) + ("…" if len(turn.transcript) > 60 else "")
    with st.expander(label, expanded=expanded):
        st.caption(
            f"{PROJECT_TYPE_LABELS[turn.project_type]} · {DETAIL_LEVEL_LABELS[turn.detail_level]} · "
            f"{OUTPUT_FORMAT_LABELS[turn.output_format]} · prompt {response.prompt_version}"
            + (f" · 📎 {', '.join(plain(n) for n in turn.attachment_names)}" if turn.attachment_names else "")
        )
        result = response.result
        if response.out_of_scope:
            st.warning(
                f"No se ha podido estimar este proyecto con la información disponible.\n\n{plain(result.summary)}",
                icon="⚠️",
            )
            return
        hours, weeks, cost, confidence = st.columns(4)
        hours.metric("Horas", result.total_hours)
        weeks.metric("Semanas", result.total_duration_weeks)
        cost.metric("Coste", f"{result.total_cost_eur:,} €".replace(",", "."))
        confidence.metric("Confianza", f"{result.confidence_pct} %")
        st.markdown(render(result, turn.output_format, turn.detail_level))


def submit_turn(transcript, files, project_type, detail_level, output_format) -> None:
    def send() -> EstimationResponse:
        return send_turn(
            st.session_state.session_id,
            transcript,
            files,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
        )

    with st.spinner("Generando la estimación..."):
        try:
            try:
                response = send()
            except SessionExpiredError:
                start_new_conversation()
                st.warning(
                    "La conversación anterior ha expirado o el servicio se ha reiniciado: "
                    "se ha empezado una conversación nueva y este turno se ha enviado en ella."
                )
                response = send()
        except EstimationAPIError as exc:
            st.error(f"⚠️ {exc}")
            return

    st.session_state.turns.append(
        Turn(transcript, [f.name for f in files], project_type, detail_level, output_format, response)
    )
    try:
        st.session_state.session_info = fetch_session(st.session_state.session_id)
    except EstimationAPIError:
        pass  # el panel lateral se actualizará en el siguiente turno


# --------------------------------------------------------------------- app

st.set_page_config(page_title="Estimador de proyectos", page_icon="🧮")
st.session_state.setdefault("turns", [])
st.session_state.setdefault("session_info", None)
st.session_state.setdefault("uploader_key", 0)
ensure_session()

st.title("🧮 Estimador de proyectos")
st.write(
    "Pega la transcripción de la reunión y, si quieres, adjunta documentos (PDF o Word). "
    "La conversación recuerda los turnos anteriores: puedes ir afinando la estimación."
)

with st.form("turn_form", clear_on_submit=False):
    transcript = st.text_area(
        "Transcripción de la reunión",
        height=200,
        max_chars=MAX_TRANSCRIPT_CHARS,
        placeholder="Ej.: El cliente quiere un portal interno para reservar salas, con login corporativo...",
    )
    files = st.file_uploader(
        "Adjuntos (opcional)",
        type=["pdf", "docx"],
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}",
    )
    col_type, col_detail, col_format = st.columns(3)
    project_type = col_type.selectbox("Tipo de proyecto", options=list(ProjectType), format_func=PROJECT_TYPE_LABELS.get)
    detail_level = col_detail.selectbox(
        "Nivel de detalle",
        options=list(DetailLevel),
        index=list(DetailLevel).index(DetailLevel.MEDIUM),
        format_func=DETAIL_LEVEL_LABELS.get,
    )
    output_format = col_format.selectbox("Formato de salida", options=list(OutputFormat), format_func=OUTPUT_FORMAT_LABELS.get)
    submitted = st.form_submit_button("Enviar", type="primary")

if submitted:
    if not transcript.strip():
        st.error("La transcripción no puede estar vacía.")
    else:
        submit_turn(transcript.strip(), files or [], project_type, detail_level, output_format)

render_sidebar()

turns: list[Turn] = st.session_state.turns
if turns:
    st.divider()
    st.subheader("Estimaciones")
    for index, turn in reversed(list(enumerate(turns, start=1))):
        render_turn(index, turn, expanded=index == len(turns))
