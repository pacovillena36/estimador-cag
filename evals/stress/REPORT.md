# Stress test del CAG — baseline (sesión 06)

> **Estado: pendiente de la ejecución completa.** El pipeline está verificado con un smoke test
> (`--max-turns 3 --repeats 1`: 45 turnos, 0 fallos, 0,33 USD), pero `results.csv` y las tablas
> de abajo se generan con la ejecución completa (900 turnos, ~15,7 USD estimados por `--dry-run`).
> Hasta entonces este reporte no contiene cifras de resultados.

## 1. Setup

- **Sistema medido:** `POST /api/v1/sessions/{id}/estimate` (CAG conversacional: ventana de 6 pares,
  anclas, resumen acumulativo y `project_metadata`), con la configuración del `.env` sin tocar:
  `openai/gpt-4o-mini` (fast) y `openai/gpt-4o` (strong), `TIER_MODE=dynamic`, modo `actor`,
  `MAX_ATTACHMENT_CHARS=20000`, `MAX_TOTAL_ATTACHMENT_CHARS=50000`. Ninguna constante de comportamiento
  se ha modificado: los únicos cambios en `app/` son el evento `turn_observed` y dos flags de depuración
  desactivados por defecto.
- **Matriz:** escenarios `growing`, `pivot` y `contradiction` (20 turnos cada uno) × adjunto de
  0/5/20/50/100k caracteres × 3 repeticiones = 900 turnos, en secuencia (concurrencia 1).
- **Presupuestos por turno** (constantes del runner, configurables con `--latency-budget-ms` y
  `--cost-budget-usd`): latencia ≤ 15 000 ms, coste ≤ 0,01 USD. Límite global
  `--max-total-cost-usd` (5 USD por defecto).
- **Commit, fecha, filas y coste observado:** en el bloque generado de abajo.

### Decisiones de diseño

- **Un evento agregado (`turn_observed`) en vez de cinco sueltos.** Una sola línea por turno con los
  13 campos se pasa a CSV de una pasada, correlaciona los campos de forma trivial (son del mismo
  turno) y no obliga a reconciliar timestamps entre eventos. El shape vive en un único tipo,
  `app/observability/turn_observation.py::TurnObservation`, que importan el servicio, la API y
  `evals/stress`. Los logs existentes se mantienen.
- **Cómo obtiene el runner `turn_observed`:** sin parsear `docker compose logs` (frágil: depende del
  buffering y del formato). El servicio guarda la última observación en la sesión y
  `GET /sessions/{id}` la expone en `last_turn_observation` solo con `EXPOSE_TURN_OBSERVATION=true`.
  Para el drift hace falta además el texto del resumen y de las anclas, que la API no exponía
  (decisión previa: pueden contener datos del cliente). Va detrás de un segundo flag,
  `EXPOSE_SESSION_MEMORY_DEBUG`. Los dos están desactivados por defecto y, sin ellos, la respuesta de
  la API es idéntica a la anterior. `docker-compose.stress.yml` los activa solo para esta prueba.
- **`cache_hit_kind`:** las sesiones nunca consultan el caché semántico (con historial, dos
  transcripciones parecidas no son peticiones equivalentes), así que solo aparecen `none` y `exact`.
  Un turno cuenta como `exact` cuando la llamada del actor sale de la caché exact-match del gateway.
- **Ubicación de las métricas:** `evals/stress/metrics.py`, no `evals/metrics.py`. Aquellas puntúan el
  `EstimationResult` final de un caso golden; estas puntúan un turno a partir de su `TurnObservation`
  y del snapshot de memoria. Reutilizan `MetricResult`, al que se añade un `score` opcional.
- **El adjunto viaja solo en el turno 1.** Mientras ese turno siga en la ventana, el adjunto viaja
  literal. Después solo sobrevive en la memoria, que es lo que mide `attachment_recall`.
- **Match literal** (casefold y espacios normalizados): una paráfrasis como "80.000 €" por "80000"
  cuenta como fallo y así se reporta. `drift_recall` busca en resumen, anclas y metadata, no en la
  ventana literal. En los turnos N ≤ 7 los facts todavía viajan en la ventana aunque la métrica no
  los vea en memoria.
- **Canarios del adjunto:** como el límite real es de 20 000 caracteres (no 60 000), el canario
  `AFTER-CAP` se coloca en el carácter ~20 300. La columna `attachment_canaries_sent` (offsets frente a
  `attachments_total_chars`) muestra exactamente dónde trunca la app.
- **Columnas extra del CSV:** `turn`, `checkpoint`, `facts_expected`, `first_fact_found` (¿sobrevive el
  nombre del proyecto?), `attachment_canaries_sent/total`, `http_status` y `error`.
- **Ojo con el tier:** con `TIER_MODE=dynamic`, los turnos con resumen (a partir del 8 con ventana de
  6) y los adjuntos de 8 000 caracteres o más van a `gpt-4o`. Es comportamiento real y no se cambia,
  pero mezcla dos modelos del mismo proveedor en la curva de coste. El tier de cada turno está en
  `last_resolved_tier`.

## 2-3. Resultados

<!-- BEGIN:generated (evals.stress.report) -->
_Pendiente: `uv run python -m evals.stress.report` tras la ejecución completa._
<!-- END:generated -->

## 4. Dónde empieza a romperse mi CAG y por qué

_Pendiente de la ejecución completa: se redactará sobre los números reales del CSV._
