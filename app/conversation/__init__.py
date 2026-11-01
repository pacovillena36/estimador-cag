"""Núcleo conversacional (arquitectura hexagonal).

- domain/: reglas puras (memoria, anclas, tiers, revisión, uso). No
  conoce FastAPI, LiteLLM, Jinja ni la configuración.
- ports.py: lo que el núcleo necesita del exterior (resumir, revisar,
  extraer metadata). El puerto del LLM es compartido: app/ports/llm.py.
- application/: casos de uso que orquestan dominio y puertos.
- adapters/: implementaciones de los puertos con el LLM y los prompts.
"""
