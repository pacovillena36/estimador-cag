# Transcripción de reunión — Ejemplo de uso

Esta transcripción es un **input de ejemplo** para probar el endpoint
`POST /api/v1/estimate`. No forma parte del contexto few-shot del sistema
(eso vive en [`app/context/examples.py`](app/context/examples.py)): es la
"reunión nueva" que se envía como parámetro para que el modelo genere una
estimación a partir de ella.

## Contexto

Reunión de descubrimiento con **"La Trattoria"**, un restaurante con dos
locales, para digitalizar la gestión de reservas y lista de espera.

## Transcripción

**Consultor:** Buenas tardes, gracias por el tiempo. Contadme un poco qué
necesitáis.

**Cliente (gerente):** Claro. Ahora mismo llevamos las reservas a mano, en
un cuaderno en cada local, y muchas veces se nos duplican mesas o se nos
olvida confirmar. Queremos algo digital.

**Consultor:** Entendido. ¿Cuántos locales tenéis y cuántas mesas por
local, más o menos?

**Cliente:** Dos locales. Uno tiene 18 mesas, el otro 12. Cada mesa tiene
una capacidad distinta, de 2 a 8 personas.

**Consultor:** ¿Qué tipo de reservas necesitáis gestionar? ¿Solo online,
solo telefónicas, ambas?

**Cliente:** Ambas. Queremos que el cliente pueda reservar desde una página
web sencilla, pero también que el camarero o el encargado pueda meter una
reserva a mano si llama alguien por teléfono.

**Consultor:** ¿Y lista de espera? Mencionaste antes algo de eso.

**Cliente:** Sí, los viernes y sábados por la noche siempre hay cola. Nos
gustaría que si no hay mesa libre, el cliente se apunte a una lista de
espera y le avisemos por SMS cuando haya mesa, en vez de que espere de pie
en la puerta.

**Consultor:** Vale. ¿Necesitáis enviar recordatorios de la reserva antes
de que llegue el cliente?

**Cliente:** Sí, un SMS o WhatsApp unas 2 horas antes estaría genial, para
reducir el "no show". No hace falta que sea complicado, con un mensaje
básico vale.

**Consultor:** ¿Tenéis algún sistema de facturación o TPV con el que esto
tendría que hablar?

**Cliente:** No, eso lo dejamos aparte por ahora. Solo reservas y lista de
espera. Más adelante quizá miremos lo del TPV, pero no ahora.

**Consultor:** ¿Quién va a usar el panel de gestión? ¿Solo vosotros los
gerentes o también los camareros?

**Cliente:** Los camareros necesitan ver la disponibilidad y añadir
reservas a mano, pero no queremos que puedan borrar reservas hechas por
otros ni cambiar la configuración de las mesas. Eso solo el gerente de cada
local.

**Consultor:** Entonces necesitaríamos al menos dos roles: gerente (control
total de su local) y camarero (ver y crear reservas, sin borrar ni
configurar). ¿Correcto?

**Cliente:** Exacto.

**Consultor:** ¿Tenéis ya una identidad de marca definida? ¿Logo, colores?

**Cliente:** Sí, tenemos logo y una paleta de colores que usamos en el
menú y en Instagram. Os la podemos pasar.

**Consultor:** Perfecto, con eso trabajamos el diseño. Última pregunta:
¿en cuánto tiempo os gustaría tener esto funcionando?

**Cliente:** Idealmente antes de la temporada alta, en unos dos meses.

**Consultor:** Tomo nota, lo tenemos en cuenta para la estimación. Os
preparamos una propuesta con el desglose de horas y os la enviamos esta
semana.
