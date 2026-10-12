# Prueba de esfuerzo del estimador: ¿cuándo deja de funcionar bien?

*Octubre de 2026 · ejecuciones del 11 y 12 de octubre · rama `session-06-stress`*

## En resumen

Hemos mantenido conversaciones largas (hasta 20 mensajes) con el estimador real, con y sin documentos
adjuntos, para ver en qué momento empieza a fallar. Estas son las conclusiones:

1. **El primer problema es el coste, y llega en el mensaje 8.** A partir de ahí cada respuesta la da el
   modelo de IA potente, que cuesta unas **13 veces más** que el barato. Una conversación de 20 mensajes
   cuesta unos 0,25 $, y **casi todo (entre el 88 % y el 97 %) se gasta del mensaje 8 en adelante**.
2. **La espera es aceptable salvo con documentos grandes.** Lo habitual son 6-9 segundos por respuesta.
   Solo 23 de 226 respuestas pasaron de 15 segundos, y todas llevaban documento adjunto.
3. **El nombre del proyecto no se olvida nunca, pero los detalles sí.** Al llegar al mensaje 20, el
   estimador recuerda de media **el 82 % de los requisitos**: se pierde aproximadamente uno de cada cinco.
4. **Las correcciones no borran el dato antiguo.** Si el cliente cambia de tecnología o corrige el
   presupuesto, el estimador **guarda el dato nuevo y el viejo a la vez**, en el 100 % de los casos.
5. **De los documentos largos solo se lee el principio.** Solo entran los primeros 20 000 caracteres:
   de un documento de 100 000 caracteres, **el 80 % no llega nunca a la IA**.

## Qué hemos probado

El estimador recuerda la conversación de cuatro formas:
- **Los 6 últimos intercambios**, literales.
- **Un resumen** de lo anterior.
- **Notas** con frases clave (presupuestos, plazos, restricciones).
- **Una ficha del proyecto** (nombre, tecnologías y alcance).

Para ponerlo a prueba simulamos a un cliente en **tres situaciones**, todas inventadas:

| Conversación | Qué ocurre | Qué queremos saber |
|---|---|---|
| **Proyecto que crece** | El proyecto "Nimbus" va sumando requisitos mensaje a mensaje | ¿Se dispara el coste? ¿Recuerda todo al final? |
| **Cambio de tecnología** | Empieza con React y en el mensaje 5 cambia a Flutter | ¿Se queda con la tecnología nueva? |
| **Presupuesto corregido** | 30 000 € en el mensaje 3, corregido a 80 000 € en el 8 | ¿Recuerda el presupuesto bueno? |

Cada conversación se probó sin documento y con documentos de 5 000 a 100 000 caracteres adjuntos en el
primer mensaje. Los documentos llevan **marcas** en posiciones conocidas, para saber hasta dónde se leen
y si se recuerdan después.

Para cada respuesta fijamos dos límites razonables: **15 segundos de espera** y **0,01 $ de coste**.

## Qué ha salido

### 1. Coste: el salto del mensaje 8

En los 7 primeros mensajes la conversación es muy barata: entre 0,01 y 0,03 $ en total sin documento. En el
mensaje 8 el estimador empieza a resumir lo más antiguo y, por diseño, desde ese momento **pasa siempre
al modelo potente**. Ocurre igual en las tres conversaciones:

- Modelo barato: **0,002 $ por respuesta**, siempre dentro del límite de coste.
- Modelo potente: **0,027 $ por respuesta**, nunca dentro del límite.

El coste no sube porque la conversación sea más larga. El estimador limita cuánto texto lee: sin
documento, el texto del mensaje 20 es solo un 30 % mayor que el del mensaje 4. Sube porque **cambia de
modelo**. Con documentos grandes la conversación llega a costar hasta 0,74 $.

### 2. Espera: crece con el texto, pero solo molesta con documentos grandes

La espera depende de cuánto texto tiene que leer la IA:
- con poco texto, unos 7 segundos;
- con mucho, habitualmente unos 16 segundos y hasta 27 en el peor caso.

Sin documento nunca se pasó de 15 segundos. **Las 23 respuestas lentas llevaban documento**, y casi todas
con documentos de 50 000 o 100 000 caracteres: mientras el documento sigue en la memoria reciente, viaja
entero con cada mensaje.

### 3. Memoria: el nombre siempre, los detalles no tanto

- **El nombre del proyecto se recordó en el 100 % de los mensajes**, en todas las conversaciones.
- **Los requisitos se van perdiendo poco a poco.** Al mensaje 20 se recuerda de media el 82 % (entre el
  74 % y el 89 % según la conversación). Lo que se pierde son detalles concretos que el resumen
  reformula o deja fuera.
- **Los datos corregidos no se borran:**
  - **Cambio de tecnología:** React sigue en la memoria en el **100 %** de los mensajes posteriores al
    cambio a Flutter. La ficha del proyecto acumula tecnologías y nunca quita ninguna.
  - **Presupuesto corregido:** los 30 000 € **reaparecen desde el mensaje 9** en las dos
    conversaciones. Cuando el mensaje 3 sale de la memoria reciente, su frase ("el presupuesto máximo es
    de 30 000 EUR") se guarda tal cual como nota, y nada la marca como obsoleta.

  En los dos casos, el estimador tiene a la vez el dato nuevo y el viejo, y depende de que la IA sepa
  cuál vale.

### 4. Documentos adjuntos: solo se lee el principio

- El estimador **solo lee los primeros 20 000 caracteres** de cada documento. De uno de 50 000 se pierde
  el 60 %; de uno de 100 000, el 80 %. Esa parte no llega nunca a la IA.
- **Lo que sí se lee, unas veces se recuerda y otras no.** Cuando el documento sale de la memoria
  reciente (mensaje 7), el resumen a veces conserva sus datos hasta el final (cambio de tecnología y
  presupuesto corregido) y otras los pierde todos (proyecto que crece). Que se recuerde o no depende
  de cómo resuma la IA en cada caso.

### 5. La caché ayuda mucho, pero solo si se repite exactamente lo mismo

Cuando una conversación se repite palabra por palabra, el sistema reutiliza las respuestas: **0,3
segundos y coste cero**, frente a 8 segundos y 0,02 $ de media de una consulta real. En la práctica, dos
clientes rara vez escriben exactamente lo mismo.

## Dónde se rompe el estimador y cuándo convendría cambiar de enfoque

**El estimador se rompe primero por coste, a partir del mensaje 8, y no por el tamaño de la
conversación.** Su forma de recordar funciona: limita el texto que lee la IA, así que la espera se
mantiene en 6-9 segundos y el nombre del proyecto no se pierde nunca. Pero la regla que lo pasa al modelo
potente en cuanto empieza a resumir multiplica por 13 el coste de cada respuesta. Ninguna respuesta del
modelo potente cumple el límite de 0,01 $, y entre el 88 % y el 97 % del gasto de una conversación llega
del mensaje 8 en adelante. La memoria aguanta razonablemente: pierde uno de cada cinco detalles al
mensaje 20. Su punto débil son las correcciones: guarda el dato viejo junto al nuevo en el 100 % de los
casos probados.

**Lo que justifica pasar a un sistema de búsqueda sobre documentos (RAG) son los documentos largos.**
Con un documento de más de 20 000 caracteres el estimador pierde información dos veces: al entrar, porque
del 60 % al 80 % del texto no se lee nunca, y al salir de la memoria reciente, porque lo leído solo
sobrevive si el resumen lo conserva. En los documentos de 50 000 y 100 000 caracteres, como mucho se
recuerda el 25 % de las marcas. Además, esos documentos son los que provocan las esperas de más de 15
segundos. Ampliar el límite de lectura o la memoria reciente solo empeoraría el coste y la espera, que ya
fallan en esos casos. Un sistema que busque en el documento solo los fragmentos relevantes para cada
pregunta es la alternativa natural.

## Hasta dónde llegan estos datos

- **La prueba no se completó.** Se paró dos veces para controlar el gasto (4,54 $ en total). Hay datos
  completos de "proyecto que crece" (salvo el documento de 100 000, cortado en el mensaje 13) y de
  "cambio de tecnología". De "presupuesto corregido" solo hay una conversación completa (sin documento)
  y otra hasta el mensaje 11.
- **Cada caso se probó de verdad una sola vez.** Las repeticiones previstas salieron enteras de la caché
  y no aportan información nueva sobre coste ni memoria. Con una sola muestra por caso, las diferencias
  pequeñas entre conversaciones pueden deberse al azar.
- **La comparación es literal.** Si el estimador escribe "80.000 €" en lugar de "80000", cuenta como no
  recordado. Así se evita juzgar a ojo, pero en los primeros mensajes el recuerdo medido es bajo: ahí
  solo cuenta la ficha del proyecto, aunque el texto original siga en la memoria reciente.

## Recomendaciones

1. **Revisar la regla que pasa al modelo potente al empezar a resumir.** Es la causa principal del coste
   y no parece que mejore la memoria.
2. **Hacer que las correcciones sustituyan al dato anterior** en la ficha y en las notas, en lugar de
   acumularse.
3. **Para documentos de más de 20 000 caracteres, probar un sistema de búsqueda (RAG)**, que es el
   siguiente paso del curso, y comparar con estos números.
4. **Completar la prueba** (presupuesto corregido con documentos) con una sola repetición y la caché
   vacía. Faltan unos 70 mensajes; por lo observado, costarían entre 1,5 y 2,5 $.

---

## Tablas

<!-- BEGIN:generated (evals.stress.report) -->

### Datos de la ejecución

- Tablas generadas el 2026-10-12 (commit `669a48a`) a partir de `results.csv`.
- 20 conversaciones y 384 mensajes enviados: 226 consultaron de verdad a la IA, 156 se sirvieron desde la caché y 2 fallaron.
- Gasto total real: 4.54 USD.
- Fallos: 2 × `http_502:estimation_failed`.

### Resumen por conversación

| Conversación | Documento | Mensajes completados | Coste | Espera habitual | Espera en el peor caso | Mensajes de más de 15 s | Recuerdo al final | ¿Recuerda el nombre? | ¿Arrastra el dato corregido? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Proyecto que crece | sin documento | 20 de 20 | 0.26 $ | 6.9 s | 9.6 s | 0 de 17 | 74% | sí | – |
| Proyecto que crece | 5k caracteres | 20 de 20 | 0.29 $ | 7.7 s | 10.8 s | 0 de 17 | 79% | sí | – |
| Proyecto que crece | 20k caracteres | 20 de 20 | 0.27 $ | 8.7 s | 12.9 s | 0 de 17 | 89% | sí | – |
| Proyecto que crece | 50k caracteres | 19 de 20 | 0.59 $ | 13.9 s | 20.8 s | 7 de 16 | 89% | sí | – |
| Proyecto que crece | 100k caracteres | 13 de 13 (cortada en el 13) | 0.21 $ | 7.2 s | 11.6 s | 0 de 13 | 100% | sí | – |
| Cambio de tecnología | sin documento | 20 de 20 | 0.25 $ | 7.6 s | 9.1 s | 0 de 20 | 82% | sí | sí |
| Cambio de tecnología | 5k caracteres | 20 de 20 | 0.37 $ | 7.6 s | 14.8 s | 1 de 20 | 88% | sí | sí |
| Cambio de tecnología | 20k caracteres | 20 de 20 | 0.31 $ | 8.1 s | 20.8 s | 2 de 20 | 82% | sí | sí |
| Cambio de tecnología | 50k caracteres | 20 de 20 | 0.30 $ | 7.6 s | 12.0 s | 0 de 20 | 76% | sí | sí |
| Cambio de tecnología | 100k caracteres | 20 de 20 | 0.74 $ | 13.3 s | 20.9 s | 8 de 20 | 76% | sí | sí |
| Presupuesto corregido | sin documento | 20 de 20 | 0.25 $ | 6.5 s | 8.5 s | 0 de 20 | 78% | sí | sí |
| Presupuesto corregido | 5k caracteres | 11 de 11 (cortada en el 11) | 0.14 $ | 10.1 s | 15.0 s | 1 de 11 | 56% | sí | sí |

### ¿Cuánto cuesta cada mensaje? (coste del mensaje → acumulado, en USD)

| Mensaje | Proyecto que crece | Cambio de tecnología | Presupuesto corregido |
|---|---:|---:|---:|
| 1 | 0.020 → 0.02 · potente | 0.012 → 0.01 · potente | 0.001 → 0.00 · barato |
| 2 | 0.001 → 0.02 · barato | 0.002 → 0.01 · barato | 0.001 → 0.00 · barato |
| 3 | 0.023 → 0.04 · potente | 0.001 → 0.02 · barato | 0.001 → 0.00 · barato |
| 4 | 0.001 → 0.05 · barato | 0.002 → 0.02 · barato | 0.001 → 0.00 · barato |
| 5 | 0.002 → 0.05 · barato | 0.002 → 0.02 · barato | 0.002 → 0.01 · barato |
| 6 | 0.005 → 0.05 · barato | 0.002 → 0.02 · barato | 0.002 → 0.01 · barato |
| 7 | 0.009 → 0.06 · barato | 0.003 → 0.03 · barato | 0.002 → 0.01 · barato |
| 8 | 0.025 → 0.09 · potente | 0.019 → 0.04 · potente | 0.020 → 0.03 · potente |
| 9 | 0.023 → 0.11 · potente | 0.025 → 0.07 · potente | 0.020 → 0.05 · potente |
| 10 | 0.034 → 0.14 · potente | 0.026 → 0.10 · potente | 0.021 → 0.07 · potente |
| 11 | 0.022 → 0.17 · potente | 0.027 → 0.12 · potente | 0.036 → 0.11 · potente |
| 12 | 0.028 → 0.19 · potente | 0.021 → 0.14 · potente | 0.019 → 0.13 · potente |
| 13 | 0.037 → 0.23 · potente | 0.029 → 0.17 · potente | 0.020 → 0.15 · potente |
| 14 | 0.030 → 0.26 · potente | 0.022 → 0.19 · potente | 0.020 → 0.17 · potente |
| 15 | 0.031 → 0.29 · potente | 0.030 → 0.23 · potente | 0.019 → 0.19 · potente |
| 16 | 0.025 → 0.32 · potente | 0.040 → 0.27 · potente | 0.019 → 0.21 · potente |
| 17 | 0.025 → 0.34 · potente | 0.029 → 0.29 · potente | 0.019 → 0.23 · potente |
| 18 | 0.026 → 0.37 · potente | 0.042 → 0.34 · potente | 0.019 → 0.25 · potente |
| 19 | 0.035 → 0.40 · potente | 0.031 → 0.37 · potente | 0.019 → 0.26 · potente |
| 20 | 0.029 → 0.43 · potente | 0.026 → 0.39 · potente | 0.019 → 0.28 · potente |

### Modelo barato frente a modelo potente

| Modelo de IA | Mensajes | Coste medio por mensaje | Espera habitual | Dentro del presupuesto de coste |
|---|---:|---:|---:|---:|
| barato (gpt-4o-mini) | 65 | 0.002 $ | 5.8 s | 100% |
| potente (gpt-4o) | 161 | 0.027 $ | 8.3 s | 0% |

### ¿Cuánto se espera según el texto que lee la IA?

| Texto que lee la IA (tokens) | Mensajes | Espera habitual | Espera en el peor caso | Coste medio |
|---|---:|---:|---:|---:|
| de 2k a 4k | 3 | 5.1 s | 5.5 s | 0.001 $ |
| de 4k a 8k | 86 | 6.9 s | 8.4 s | 0.013 $ |
| de 8k a 16k | 105 | 9.0 s | 14.2 s | 0.020 $ |
| de 16k a 32k | 28 | 15.6 s | 24.7 s | 0.040 $ |
| más de 32k | 4 | 20.5 s | 26.9 s | 0.036 $ |

### ¿Cuánto recuerda a medida que avanza la conversación?

| Conversación | Mensaje | Datos a recordar | Recuerdo medio | Recuerda el nombre | Arrastra el dato corregido | Conversaciones |
|---|---:|---:|---:|---:|---:|---:|
| Proyecto que crece | 1 | 0 | – | – | – | 5 |
| Proyecto que crece | 3 | 2 | 90% | 100% | – | 5 |
| Proyecto que crece | 6 | 5 | 76% | 100% | – | 5 |
| Proyecto que crece | 10 | 9 | 93% | 100% | – | 5 |
| Proyecto que crece | 20 | 19 | 83% | 100% | – | 4 |
| Cambio de tecnología | 1 | 0 | – | – | – | 5 |
| Cambio de tecnología | 3 | 2 | 70% | 100% | – | 5 |
| Cambio de tecnología | 6 | 3 | 80% | 100% | 100% | 5 |
| Cambio de tecnología | 10 | 7 | 77% | 100% | 100% | 5 |
| Cambio de tecnología | 20 | 17 | 81% | 100% | 100% | 5 |
| Presupuesto corregido | 1 | 0 | – | – | – | 2 |
| Presupuesto corregido | 3 | 2 | 75% | 100% | – | 2 |
| Presupuesto corregido | 6 | 5 | 30% | 100% | – | 2 |
| Presupuesto corregido | 10 | 8 | 56% | 100% | 100% | 2 |
| Presupuesto corregido | 20 | 18 | 78% | 100% | 100% | 1 |

### ¿Qué pasa con los documentos adjuntos?

| Documento adjunto | Caracteres que lee la IA | Marcas que llegan a la IA | Marcas que recuerda al final |
|---|---:|---:|---:|
| 5k caracteres | 4956 | 3 de 3 | 67% |
| 20k caracteres | 19996 | 3 de 3 | 50% |
| 50k caracteres | 20029 | 1 de 4 | 25% |
| 100k caracteres | 20029 | 1 de 4 | 12% |

### Efecto de la caché

| Tipo de respuesta | Mensajes | Espera habitual | Coste medio |
|---|---:|---:|---:|
| Consulta real a la IA | 226 | 8.0 s | 0.020 $ |
| Respuesta reutilizada (caché) | 156 | 0.3 s | 0.000 $ |

<!-- END:generated -->

---

## Anexo técnico

- **Sistema medido:** `POST /api/v1/sessions/{id}/estimate` con la configuración habitual, sin tocar
  ninguna constante de comportamiento: `gpt-4o-mini` (barato) y `gpt-4o` (potente), tier dinámico,
  ventana de 6 pares y `MAX_ATTACHMENT_CHARS=20000`. Los únicos cambios en `app/` son el evento
  `turn_observed` y dos interruptores de depuración desactivados por defecto.
- **Cómo se mide:** el servicio emite un único evento agregado por turno (`turn_observed`, 13 campos).
  Así cada turno es una fila de CSV, sin cruzar varios logs ni reconciliar horas. El runner no lee logs:
  consulta `GET /sessions/{id}`, que devuelve la observación del último turno y el texto de la memoria
  solo con `EXPOSE_TURN_OBSERVATION` y `EXPOSE_SESSION_MEMORY_DEBUG` (`docker-compose.stress.yml`).
- **Métricas** (`evals/stress/metrics.py`, separadas de las evals de calidad porque puntúan turnos y no
  estimaciones finales):
  - latencia ≤ 15 000 ms;
  - coste ≤ 0,01 USD;
  - *memory drift*: un dato está recordado si aparece literal (sin distinguir mayúsculas) en el resumen,
    las notas o la ficha.
- **Decisiones:**
  - El adjunto va solo en el primer mensaje.
  - Coste y espera se calculan sin aciertos de caché.
  - La memoria se mide en la primera repetición de cada conversación.
  - El acierto de caché se cuenta cuando la llamada principal sale de la caché exacta; las sesiones no
    usan la caché semántica.
- **Pendiente técnico:**
  - En los aciertos de caché, `tokens_in` y `tokens_out` no son 0, como pedía el contrato.
  - El CSV no guarda la regla que eligió el modelo.
- **Reproducir:** `uv run python -m evals.stress.run --help` y `uv run python -m evals.stress.report`.
  En este equipo, Windows bloquea una librería, así que se ejecutan en Docker.
