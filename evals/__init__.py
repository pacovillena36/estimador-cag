"""Framework de evals del estimador conversacional.

- dataset.py: casos golden (golden_dataset.json) validados con Pydantic.
- metrics.py: métricas binarias (pasa / no pasa) sobre la estimación final.
- runner.py: ejecuta los casos a través de un puerto (CaseExecutor) y
  agrega resultados, coste y latencia. No sabe nada de LLMs ni de FastAPI.
- executor.py: adaptador del puerto con el servicio real de sesiones.
- run.py: CLI (uv run python -m evals.run --mode actor|acb).
"""
