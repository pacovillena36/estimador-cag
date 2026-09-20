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
from app.services.llm_service import generate_estimation  # noqa: E402

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

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

transcription = st.chat_input("Pega aquí la transcripción de la reunión...")

if transcription:
    st.session_state.messages.append({"role": "user", "content": transcription})
    with st.chat_message("user"):
        st.markdown(transcription)

    with st.chat_message("assistant"):
        with st.spinner("Generando estimación..."):
            try:
                estimation = generate_estimation(transcription)
            except Exception as exc:
                estimation = f"⚠️ Error al generar la estimación: {exc}"
        st.markdown(estimation)

    st.session_state.messages.append({"role": "assistant", "content": estimation})
