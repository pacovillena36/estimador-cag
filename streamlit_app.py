"""Interfaz conversacional en Streamlit para el Estimador CAG.

Reutiliza la lógica de construcción del prompt CAG y de llamada al LLM ya
implementada en app/services/llm_service.py; esta app solo se encarga de la
interfaz de chat.
"""

import os

import streamlit as st

# app.config instancia Settings (y por tanto lee .env / variables de entorno)
# en el momento de importarse. Para poder desplegar en Streamlit Cloud (donde
# no existe .env) sin tocar app/config.py, volcamos a os.environ las claves
# presentes en st.secrets que aún no estén definidas, antes de importar.
_SECRET_ENV_KEYS = (
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_WORKSPACE_ID",
    "ANTHROPIC_MODEL",
)
try:
    for _key in _SECRET_ENV_KEYS:
        if _key not in os.environ and _key in st.secrets:
            os.environ[_key] = str(st.secrets[_key])
except Exception:
    pass  # No hay secrets.toml (por ejemplo, en local con solo .env)

from app.config import settings  # noqa: E402
from app.context.examples import ESTIMATION_EXAMPLES  # noqa: E402
from app.services.llm_service import build_system_prompt, generate_estimation_stream  # noqa: E402

st.set_page_config(page_title="Estimador CAG", page_icon="🧮")

st.title("🧮 Estimador CAG")
active_model = (
    settings.anthropic_model
    if settings.llm_provider == "anthropic"
    else settings.openai_model
)
st.caption(f"Proveedor: **{settings.llm_provider}** · Modelo: **{active_model}**")
st.write(
    "Pega la transcripción (o resumen) de una reunión con el cliente y "
    "recibirás una estimación de esfuerzo generada con el mismo formato y "
    "criterio que los ejemplos de referencia del sistema."
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_metrics" not in st.session_state:
    st.session_state.last_metrics = None

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

transcription = st.chat_input("Pega aquí la transcripción de la reunión...")

if transcription:
    st.session_state.messages.append({"role": "user", "content": transcription})
    with st.chat_message("user"):
        st.markdown(transcription)

    with st.chat_message("assistant"):
        try:
            chunks, metrics = generate_estimation_stream(transcription)
            # st.write_stream consume el iterador (que llama a la API del
            # proveedor en modo stream=True) y va pintando cada delta de
            # texto en cuanto llega; devuelve la respuesta completa acumulada.
            # Al agotarse, `metrics` queda relleno con modelo/tokens/tiempo.
            estimation = st.write_stream(chunks)
            st.session_state.last_metrics = metrics
        except Exception as exc:
            estimation = f"⚠️ Error al generar la estimación: {exc}"
            st.markdown(estimation)

    st.session_state.messages.append({"role": "assistant", "content": estimation})

with st.sidebar:
    st.header("Contexto CAG")

    with st.expander("System prompt activo"):
        st.text_area(
            "system prompt",
            value=build_system_prompt(),
            height=400,
            disabled=True,
            label_visibility="collapsed",
        )

    with st.expander(f"Ejemplos inyectados ({len(ESTIMATION_EXAMPLES)})"):
        for i, example in enumerate(ESTIMATION_EXAMPLES, start=1):
            st.markdown(f"**Ejemplo {i} — resumen de reunión**")
            st.markdown(example["meeting_summary"])
            st.markdown("**Estimación generada:**")
            st.markdown(example["estimation"])
            if i < len(ESTIMATION_EXAMPLES):
                st.divider()

    st.header("Última llamada")
    metrics = st.session_state.last_metrics
    if metrics is None:
        st.caption("Todavía no se ha generado ninguna estimación.")
    else:
        st.metric("Modelo", metrics.model)
        col1, col2 = st.columns(2)
        col1.metric("Tokens entrada", metrics.input_tokens)
        col2.metric("Tokens salida", metrics.output_tokens)
        if metrics.elapsed_seconds is not None:
            st.metric("Tiempo de respuesta", f"{metrics.elapsed_seconds:.2f} s")
