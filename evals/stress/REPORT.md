# Stress test del CAG — baseline (sesión 06)

> **Estado: ejecución PARCIAL.** `results.csv` (ejecución del 2026-10-11 sobre el commit `dad57e6`)
> tiene 253 de las 900 filas previstas: solo el escenario `growing`, completo para los adjuntos de
> 0/5/20/50 KB × 3 repeticiones y cortado en el turno 13 de `growing · 100 KB · rep 1`. Faltan
> `pivot` y `contradiction`. El corte no lo provocó `--max-total-cost-usd`, porque el coste
> acumulado era de 2,18 USD; el CSV no registra la causa. Las conclusiones de abajo valen solo para
> `growing`. Lo que dependa del `stale_fact` (pivote y contradicción) queda **sin medir**.

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

### Limitaciones de esta ejecución (leer antes de las tablas)

1. **Solo 95 de los 251 turnos completados llamaron de verdad al LLM.** Los otros 156 son cache hits
   `exact` del gateway:
   - Las repeticiones 2 y 3 reproducen la 1 entera: 20 de 20 hits, coste 0 y unos 300-700 ms. Una
     respuesta cacheada deja el historial idéntico y el siguiente turno vuelve a dar hit.
   - Los turnos 1 a 3 de la repetición 1 también son hits, porque son los mismos transcripts del
     smoke test del día anterior en el mismo contenedor (caché en memoria con TTL de 1 h).
   - Solo rompen la cadena los dos 502 de 50 KB (turno 7 de la rep. 1 y turno 19 de la rep. 2): el
     turno fallido no queda en caché y la repetición siguiente diverge desde ahí.

   Por eso las cifras de coste y latencia del CAG se leen **sin cache hits**, que equivale a una sola
   conversación real por tamaño de adjunto (n = 1) y no da para hablar de varianza. Los turnos 1-3
   sin caché solo existen para 100 KB, así que en la curva 2 esas filas no son comparables con el
   resto.
2. **`tokens_in`/`tokens_out` no valen 0 en los cache hits.** El gateway replica los tokens de la
   respuesta cacheada con `cost_usd = 0`, y `TurnObservation` los suma. El contrato pedía 0 sin
   llamada al LLM. No afecta a las tablas sin caché, pero hay que corregirlo antes de la próxima
   ejecución.
3. **Adjuntos de 50 y 100 KB:** los dos llegan al LLM con **20 029 caracteres**, por el truncado a
   `MAX_ATTACHMENT_CHARS`. Lo que haya de diferencia entre ellos es variabilidad del LLM (sobre todo
   la longitud de la salida), no tamaño de entrada.
4. **El CSV no guarda la regla del tier**, solo el tier resuelto. Algunos turnos van a strong sin
   que se pueda saber por qué (por ejemplo, el turno 6 de 0 KB).

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

### Setup de la ejecución

- Tablas generadas: 2026-10-12 14:50 UTC · commit `dad57e6` · CSV `results.csv`
- Filas: 253 (251 turnos completados, 2 fallidos) en 13 conversaciones
- Turnos por tier: {'fast': 62, 'strong': 189}
- Coste total observado: 2.1764 USD
- Errores: `http_502:estimation_failed` x2

### Tabla resumen (escenario × adjunto)

| escenario | adjunto KB | turnos | P50 ms | P95 ms | P50 ms sin caché | P95 ms sin caché | coste USD | exact hit | semantic hit | recall medio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| growing | 0 | 60 | 340 | 7996 | 6889 | 9619 | 0.2630 | 72% | 0% | 92% |
| growing | 5 | 60 | 398 | 10057 | 7690 | 10838 | 0.2898 | 72% | 0% | 84% |
| growing | 20 | 60 | 300 | 9521 | 8734 | 12876 | 0.2706 | 72% | 0% | 76% |
| growing | 50 | 58 | 7368 | 18764 | 13799 | 21112 | 1.1390 | 47% | 0% | 96% |
| growing | 100 | 13 | 7156 | 11582 | 7156 | 11582 | 0.2140 | 0% | 0% | 86% |

### Curva 1 · latency_ms vs tokens_in (sin cache hits)

| tokens_in (turno) | turnos | tokens_in medio | P50 ms | P95 ms | coste medio USD |
|---|---:|---:|---:|---:|---:|
| 4k–8k | 28 | 6769 | 6862 | 8226 | 0.01372 |
| 8k–16k | 55 | 10398 | 9438 | 14110 | 0.02235 |
| 16k–32k | 10 | 21986 | 16574 | 25653 | 0.05496 |
| >= 32k | 2 | 38512 | 19486 | 20413 | 0.00657 |

### Curva 2 · cost_usd acumulado vs turn_index

Cada celda: coste medio del turno / acumulado de esas medias (USD), solo turnos sin cache hit, todos los tamaños de adjunto; entre corchetes, el tier mayoritario del turno.

| turno | growing |
|---|---:|
| 1 | 0.01983 / 0.0198 [strong] |
| 2 | 0.00131 / 0.0211 [fast] |
| 3 | 0.02318 / 0.0443 [strong] |
| 4 | 0.00141 / 0.0457 [fast] |
| 5 | 0.00184 / 0.0476 [fast] |
| 6 | 0.00465 / 0.0522 [fast] |
| 7 | 0.00943 / 0.0616 [fast] |
| 8 | 0.02494 / 0.0866 [strong] |
| 9 | 0.02335 / 0.1099 [strong] |
| 10 | 0.03383 / 0.1438 [strong] |
| 11 | 0.02192 / 0.1657 [strong] |
| 12 | 0.02766 / 0.1933 [strong] |
| 13 | 0.03724 / 0.2306 [strong] |
| 14 | 0.02978 / 0.2604 [strong] |
| 15 | 0.03088 / 0.2912 [strong] |
| 16 | 0.02476 / 0.3160 [strong] |
| 17 | 0.02530 / 0.3413 [strong] |
| 18 | 0.02592 / 0.3672 [strong] |
| 19 | 0.03458 / 0.4018 [strong] |
| 20 | 0.02929 / 0.4311 [strong] |

### Coste y latencia por tier (sin cache hits)

| tier | turnos | tokens_in medio | tokens_out medio | coste medio USD | P50 ms | cost_ok | latency_ok |
|---|---:|---:|---:|---:|---:|---:|---:|
| fast | 19 | 13276 | 705 | 0.00237 | 6742 | 100% | 89% |
| strong | 76 | 10606 | 1138 | 0.02805 | 9034 | 0% | 88% |

### Curva 3 · MemoryDriftMetric (recall) vs N

| escenario | N | muestras | facts | recall | fact turno 1 | stale presente | summary chars | anclas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| growing | 1 | 13 | 0 | – | – | – | 0 | 0.0 |
| growing | 3 | 13 | 2 | 88% | 100% | – | 0 | 0.0 |
| growing | 6 | 13 | 5 | 78% | 100% | – | 0 | 0.0 |
| growing | 10 | 13 | 9 | 94% | 100% | – | 1048 | 2.0 |
| growing | 20 | 12 | 19 | 83% | 100% | – | 2542 | 4.0 |

### Recall de canarios del adjunto (truncado)

| adjunto KB | chars enviados (turno 1) | canarios | canarios enviados | recall turno 1 | recall final | latencia turno 1 ms | tier turno 1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5 | 4956 | 3 | 3.0 | 0% | 0% (turno 20) | 462 | fast:3 |
| 20 | 19996 | 3 | 3.0 | 0% | 0% (turno 20) | 431 | strong:3 |
| 50 | 20029 | 4 | 1.0 | 0% | 25% (turno 20) | 528 | strong:3 |
| 100 | 20029 | 4 | 1.0 | 0% | 0% (turno 13) | 2750 | strong:1 |

<!-- END:generated -->

## 4. Dónde empieza a romperse mi CAG y por qué

**El CAG se rompe primero por coste, a partir del turno 7-8, y la causa es la política de tier, no
el crecimiento del contexto.**
- **La ventana acota la entrada.** `messages_in_window` se queda en 12 desde el turno 6. En la
  conversación sin adjunto, `tokens_in` pasa de 4 498 en el turno 4 a 5 830 en el turno 20 (×1,3).
- **El coste por turno se multiplica por 13.** Pasa de 0,00090 a 0,01184 USD, porque cuando aparece
  el resumen todos los turnos pasan a gpt-4o (coincide con la regla `long_conversation`, aunque el
  CSV no guarda la regla; ver limitación 4).
- **Por tier:** un turno strong cuesta de media 0,0281 USD frente a 0,0024 USD uno fast (×11,8).
  Ningún turno strong cumple el presupuesto de 0,01 USD; todos los fast lo cumplen.
- **En conversación:** una conversación `growing` real cuesta unos 0,43 USD (suma de las medias de
  la curva 2), y más del 85 % se gasta del turno 8 en adelante.
- **Latencia:** crece con los tokens (P50 de 6,9 s entre 4k y 8k tokens, 9,4 s entre 8k y 16k y
  16,6 s entre 16k y 32k), pero solo rompe el presupuesto de 15 s con el adjunto de 50 KB. Ahí caen
  los 11 turnos lentos de los 95 reales (P95 de 21,1 s), porque el adjunto viaja literal en la
  ventana hasta el turno 7 y las estimaciones salen largas (1 744 tokens de salida de media).

**La memoria aguanta mejor de lo esperado en `growing`; donde de verdad falla es con los documentos
largos.**
- **Nombre del proyecto:** "Nimbus" aparece en memoria en el 100 % de los turnos (el CSV no
  guarda si está en el resumen, en las anclas o en la metadata).
- **Recall de los requisitos:** en la conversación sin adjunto se mantiene en el 100 % hasta N = 10
  y cae al 92 % en N = 13 y al **74 % en N = 20** (14 de 19 requisitos). La media de N = 20 en los
  cuatro tamaños completos es del 83 %. El resumen termina en unos 2 100-2 900 caracteres y solo hay
  4 anclas, porque las reglas de anclas solo capturan frases con presupuesto, plazo, equipo,
  restricción o decisión técnica.
- **Primeros turnos:** el recall es irregular (50 % en N = 3 con 20 KB) porque antes de que haya
  resumen solo cuenta la metadata. El texto sigue en la ventana, pero la métrica no la mira.
- **Adjuntos:** el truncado deja fuera lo que pasa de 20 029 caracteres. Con 50 y 100 KB solo llega
  el canario START (1 de 4), así que el 60-80 % del documento **no llega nunca al LLM**. Incluso lo
  que sí llega desaparece al salir de la ventana: el recall de canarios en memoria es del 0 % en
  todos los tamaños, salvo un 25 % en 50 KB, rep. 1, desde el turno 8.

**Caso límite que justificaría RAG:** un documento de más de 20 000 caracteres cuyos detalles haya
que recuperar después de que su turno salga de la ventana (turno 8 o siguientes). Ahí el CAG pierde
el contenido por partida doble: por truncado al entrar y por resumen al salir. Ampliar el límite o
la ventana solo traslada el problema al coste y la latencia, que ya fallan con 50 KB.

### Pendiente para cerrar el baseline

- Ejecutar `pivot` y `contradiction` para medir `stale_fact_present`, con
  `--scenarios pivot,contradiction` (sin `growing`).
- Reiniciar la API antes de ejecutar (vacía la caché del smoke test) y valorar `--repeats 1`: las
  repeticiones con transcripts idénticos solo miden la caché.
- Corregir `tokens_in`/`tokens_out` en los cache hits (limitación 2) y guardar la regla del tier en
  la observación o en el CSV.
