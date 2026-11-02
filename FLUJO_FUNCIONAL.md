# Flujo funcional del estimador

Así funciona la aplicación vista desde fuera, siguiendo el camino de una transcripción desde que la envías hasta que recibes la estimación, y después qué cambia cuando sigues hablando en la misma conversación. Al final se describe el archivo de presupuestos históricos y lo que todavía falta.

## Situación actual

La aplicación tiene **dos flujos que hoy no están conectados**:

| Flujo | Qué hace | Quién lo usa | Qué no hace |
|---|---|---|---|
| **Estimación** (secciones 0 a 11) | Convierte una transcripción o una descripción en una estimación con el modelo | Streamlit, con las sesiones | No consulta los presupuestos históricos |
| **Archivo de presupuestos** (sección 12) | Guarda presupuestos históricos y busca los fragmentos más parecidos a un texto | `seed` para la carga; `/search` y `/embeddings/encode` no tienen ningún llamador dentro de la aplicación | No genera estimaciones: solo devuelve lo encontrado |

Por tanto, **las estimaciones se generan solo con el modelo, sin apoyarse en proyectos anteriores**. El diagnóstico completo, con sus pruebas y la propuesta de evolución, está en `arquitectura-actual.md`.

---

## 0. Antes de nada: la conversación (sesión)

El cliente (Streamlit u otro) abre primero una **sesión** con `POST /api/v1/sessions` y recibe un `session_id`. Ese identificador funciona como una llave de acceso: quien lo tiene, entra en esa conversación.

- Las sesiones se guardan **en la memoria del servidor**. Si el servicio se reinicia, se pierden.
- Caducan tras **60 minutos sin uso** y hay un máximo de **1000** a la vez. Si se llega al máximo, el servidor responde 503 en vez de echar la conversación de otra persona.
- Si una sesión ha caducado, el servidor responde 404 y Streamlit crea otra sin que hagas nada y reenvía el mensaje.

## 1. Envías un turno

`POST /api/v1/sessions/{id}/estimate` con:

- la **transcripción** de la reunión (obligatoria, sin espacios sobrantes y con un máximo de 20.000 caracteres por defecto),
- el **tipo de proyecto**, el **nivel de detalle** y el **formato de salida**,
- **adjuntos** PDF o DOCX (opcionales),
- `mode` (opcional): `actor` (una sola respuesta del modelo) o `acb` (respuesta revisada).

Si la transcripción está vacía o es demasiado larga, la petición se rechaza al momento (422).

## 2. Control de los adjuntos (antes de bloquear la sesión)

Antes de tocar la conversación se revisan los ficheros: cuántos son, cuánto ocupan, y si son **de verdad** PDF o DOCX (se mira el contenido del fichero, no solo el nombre). También se protege contra ficheros comprimidos que se inflan al abrirlos ("zip bombs"). Si un adjunto falla, la respuesta es un 413, 415 o 422 y la conversación no se ha tocado.

## 3. Se bloquea la sesión

A partir de aquí **solo se procesa un mensaje a la vez por conversación**. Si llegan dos a la vez, el segundo espera a que termine el primero, para que nunca se mezclen el historial ni los datos del proyecto.

## 4. Extracción del texto de los adjuntos

Se saca el texto de los PDF (solo las primeras páginas) y de los DOCX, con un límite de caracteres. Si se recorta, se marca como truncado. Además se neutralizan las etiquetas especiales del prompt, para que un documento no pueda hacerse pasar por instrucciones.

## 5. Filtros de entrada (guardrails)

La transcripción y el texto de los adjuntos pasan, en este orden, por:

1. **Detección de prompt injection**: intentos de dar órdenes al modelo ("ignora las instrucciones…").
2. **Detección de datos personales** (emails, teléfonos, DNI…).
3. **Moderación** con la API de OpenAI (contenido dañino).

Los dos primeros están por defecto en modo *solo registrar* (dejan constancia en los logs pero no bloquean). La moderación está en modo *bloquear*: si salta, la respuesta es un **400** con un mensaje genérico. Si la API de moderación no responde, se rechaza por precaución con un **503**: sin moderar no se sigue.

## 6. Se monta el mensaje y se elige el modelo

- **Mensaje de usuario**: la transcripción entre `<transcript>` y cada adjunto entre `<attachment_content>`.
- **Elección del modelo según la dificultad**: se aplican reglas fijas (sin IA), en orden, y gana la primera que se cumple:
  1. adjuntos grandes (≥ 8.000 caracteres) → modelo **potente**
  2. transcripción larga (≥ 6.000) → potente
  3. la estimación anterior tuvo **confianza baja** (< 60 %) → potente
  4. palabras que indican complejidad (migración, legacy, tiempo real, RGPD, microservicios…) → potente
  5. la conversación ya es larga (existe un resumen de turnos antiguos) → potente
  6. si no se cumple ninguna → modelo **rápido** y barato

  La regla que ha decidido queda guardada, así se puede explicar por qué se usó cada modelo.

## 7. Se construye lo que se envía al modelo

1. **Instrucciones del sistema** (prompt v5), que se generan de nuevo en cada turno e incluyen:
   - criterios para estimar según el tipo de proyecto,
   - la sección de **alcance**: si no es un proyecto de software o la confianza saldría por debajo del mínimo, el modelo debe contestar "fuera de alcance" en vez de inventarse una estimación,
   - `<project_metadata>`: los datos del proyecto acumulados en la conversación,
   - `<conversation_summary>` y `<anchors>`: la memoria de los turnos antiguos (explicada en la sección de varias iteraciones).
2. Los **últimos turnos completos** (pregunta + respuesta) tal cual se dijeron.
3. El **mensaje actual**.

## 8. Llamada al modelo

- **Una sola respuesta** (modo `actor`, el de por defecto): se pide al modelo una estimación con una estructura fija (fases con horas, semanas, coste y confianza; totales; resumen; suposiciones). Esa respuesta se comprueba automáticamente: los totales tienen que cuadrar con la suma de las fases, la confianza tiene que llegar al mínimo, etc. Si no cumple, se le devuelven los errores al modelo para que corrija, unas cuantas veces. Si aun así no vale → **502** y **la conversación no cambia**.
- **Respuesta revisada** (modo `acb`): después de esa primera estimación, un **crítico** la revisa. Si la da por buena, se usa tal cual. Si pide cambios, un **jefe** (siempre con el modelo potente) decide cuál es la estimación final. Si el crítico o el jefe fallan, se usa la estimación original: la revisión mejora la respuesta, pero nunca hace fallar el turno.
- **Proveedores**: si un proveedor falla, se reintenta y, si sigue fallando, se pasa al otro (OpenAI ↔ Anthropic). Si fallan todos → **503**. También hay una caché que devuelve la respuesta guardada cuando se envía exactamente la misma petición.

## 9. Filtros de salida

- Se marca si la estimación es "fuera de alcance".
- Se buscan datos personales en los textos de la respuesta (en modo *solo registrar* por defecto; en modo *bloquear* se sustituirían por una versión tapada).

## 10. Actualización de la memoria (solo si todo ha ido bien)

Se describe en la sección siguiente. Lo importante: **la memoria solo avanza con turnos que han terminado bien**. Cualquier error en los pasos anteriores deja la conversación igual que estaba.

## 11. Respuesta

Se guarda el gasto del turno (tokens, coste, tiempo, modelo elegido y regla que lo decidió) y se registra un resumen del turno en los logs. Después se devuelve la **estimación estructurada**. El cliente decide cómo pintarla (tabla, lista…); el servidor siempre devuelve los mismos datos. Con `GET /sessions/{id}` puedes ver el estado: datos del proyecto, número de turnos, gasto y modelo usado.

> Nota: el endpoint sin conversación (`POST /api/v1/estimate`) recorre un camino parecido pero **sin memoria** y con una descripción de 2.000 caracteres como máximo por defecto. Ese sí usa una **caché por similitud** (Redis + embeddings: guarda respuestas a descripciones parecidas). Por defecto está en modo `shadow`: consulta y escribe en la caché para medir, pero siempre responde con el modelo; solo en modo `active` devuelve la respuesta guardada. En las sesiones no se usa, porque con historial dos transcripciones parecidas no piden lo mismo. Streamlit no llama a este endpoint.

---

## Qué pasa en varias iteraciones sobre la misma conversación

La memoria tiene **tres capas** y además una ficha estructurada del proyecto.

### a) Últimos turnos, tal cual

Se guardan los últimos **6 turnos completos** (pregunta + respuesta; la respuesta es la estimación en JSON). En cada nuevo turno se envían enteros al modelo, así puedes decir "súbele un 20 % a la fase de backend" y el modelo sabe a qué te refieres.

### b) Frases clave (anclas)

De cada transcripción se sacan, con reglas fijas (sin IA), las frases que hablan de **presupuesto** ("máximo 40.000 €"), **equipo** ("equipo de 4 personas"), **plazos** ("antes de junio", "3 meses"), **restricciones** ("es obligatorio…", "no incluye…") y **decisiones técnicas** ("usaremos React"). Se guardan **literales**.

Mientras su turno está entre los últimos 6, no se repiten, porque ya viajan dentro del propio turno. Cuando el turno sale de la ventana, sus frases clave pasan a `<anchors>`: se eliminan las repetidas y se guardan como mucho 12 (se descartan las más antiguas).

### c) Resumen acumulado

Cuando entra el turno 7 y el 1 sale de la ventana, se hace una llamada barata al modelo: *resumen anterior + turno que sale → resumen nuevo*. Ese resumen va en `<conversation_summary>`. Si la llamada falla, se conserva el resumen anterior y el turno sigue adelante.

### d) Ficha del proyecto

En **cada** turno, otra llamada al modelo saca de la conversación el nombre del proyecto, el tamaño del equipo, las tecnologías mencionadas y el alcance acordado, y los **combina** con lo que ya había, sin borrar lo anterior a ciegas:

- nombre y equipo: solo se actualizan si el dato nuevo existe,
- tecnologías: se suman a la lista sin repetir (máximo 30),
- alcance: se sustituye si llega uno nuevo.

Se guarda en `<project_metadata>`. El prompt indica que **si la transcripción actual contradice estos datos, manda la transcripción actual**.

### Efectos sobre los turnos siguientes

- **Elección del modelo**: si la estimación anterior tuvo confianza < 60 %, el siguiente turno usa el modelo potente. Y en cuanto existe un resumen (a partir del turno 7), **todos** los turnos usan el modelo potente ("conversación larga"), salvo que antes se cumpla otra regla.
- **Coherencia**: el prompt pide mantener el mismo proyecto y el mismo nombre, y respetar las frases clave salvo que se cambien de forma explícita.
- **Coste**: a partir del 7º turno cada mensaje hace hasta 3 llamadas (estimación + resumen + ficha), o 5 en modo `acb`. Todas suman en el gasto de la sesión.

### Ejemplo de cronología

| Turno | Lo que recibe el modelo | Después del turno |
|---|---|---|
| 1 | instrucciones (ficha vacía) + mensaje | se guarda el turno 1; se rellena la ficha |
| 2–6 | instrucciones (con ficha) + turnos anteriores + mensaje | crece la ventana; la ficha se va completando |
| 7 | instrucciones + turnos 1–6 + mensaje | el turno 1 sale: sus frases clave pasan a anclas y se crea el resumen |
| 8+ | instrucciones (ficha + resumen + anclas) + últimos 6 turnos + mensaje | modelo potente por "conversación larga"; el resumen se amplía con cada turno que sale |

"Nueva conversación" borra la sesión (`DELETE`) y empieza de cero.

---

## 12. El archivo de presupuestos históricos

Este flujo funciona por su cuenta: nada de lo que hace llega a las estimaciones de las secciones anteriores.

### Carga al arrancar

Con `docker compose up -d`, los servicios arrancan en este orden:

1. **`postgres`** se levanta y espera a estar listo.
2. **`migrate`** crea o actualiza las tablas y termina.
3. **`api`** arranca y queda disponible.
4. **`seed`** carga los presupuestos de `data/` a través de `POST /embeddings/ingest` y termina.

Repetir el arranque no duplica nada: un presupuesto ya cargado se salta. El archivo tiene ahora **15 presupuestos y 52 fragmentos (chunks)** de los sectores finance, ecommerce, healthcare e industrial.

### Cargar un presupuesto (`POST /embeddings/ingest`)

1. Se comprueba el tamaño de la petición (2 MB como máximo por defecto) y el formato del presupuesto.
2. Si ya existe uno con el mismo `source_path`, la respuesta es un **409**.
3. Cada componente del presupuesto se convierte en un fragmento, con una cabecera que indica su proyecto, sector, año y tecnología principal.
4. Se pide a OpenAI el embedding de cada fragmento (`text-embedding-3-small`, 1536 dimensiones). Si falla, la respuesta es un **502** y no se guarda nada.
5. El presupuesto y sus fragmentos se guardan juntos en PostgreSQL. Las horas, el sector y otros datos quedan como metadatos.

### Buscar (`POST /search`)

Recibe un texto (`query`) y cuántos resultados quiere (`k`, 5 por defecto y 50 como máximo):

1. Si el texto supera los **2.000 caracteres**, la respuesta es un **422**.
2. Se calcula el embedding del texto y se ordenan todos los fragmentos por distancia coseno.
3. Se devuelven los `k` más cercanos con su contenido, distancia y metadatos.

Solo ordena por parecido: no filtra por sector, no limita los resultados por presupuesto y no descarta los poco parecidos. Tampoco pasa por los filtros de entrada de la sección 5.

### Calcular un embedding (`POST /embeddings/encode`)

Recibe de 1 a 32 textos y devuelve el vector de cada uno, comparable con los del archivo, sin guardar nada. Un texto de más de 8.191 tokens se rechaza con un **422**: no se recorta.

---

## 13. Lo que falta hoy

Comprobado con la reunión de ejemplo `examples/transcripts/02_ambiguous.txt`:

- **La búsqueda no llega a la estimación.** Lo que encuentra se devuelve a quien llama y ahí termina.
- **Una transcripción normal puede no entrar en la búsqueda.** Esa reunión, de unos 5 minutos, tiene 2.853 caracteres, así que recibe un 422, aunque las sesiones sí la aceptarían.
- **Los resultados son poco precisos.** Al buscar con el embedding de la reunión completa, los 5 resultados son de ecommerce, pero solo 1 es claramente relevante, y 4 proceden del mismo presupuesto.
- **Nada avisa de lo que falta.** No hay ningún proyecto de fidelización en el archivo, pero siempre se devuelven `k` resultados.

Además:

- **No hay usuarios ni permisos.** No existe backend de negocio ni inicio de sesión: Streamlit llama directamente al servicio IA. Los puertos solo se publican en `127.0.0.1`.
- **Las conversaciones no se conservan.** Viven en la memoria del servidor, como explica la sección 0.
- **No queda registro de lo recuperado.** Los logs de la búsqueda guardan cuántos resultados hubo y el tiempo, pero no cuáles fueron.

La evolución propuesta para cerrar estos puntos está en la sección 4 de `arquitectura-actual.md`. **Es solo una propuesta: nada de eso está construido.**
