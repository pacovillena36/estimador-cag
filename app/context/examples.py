"""Ejemplos de estimaciones previas (few-shot) inyectados en el prompt.

Estos ejemplos representan el "conocimiento" del sistema CAG: estimaciones
históricas reales (o representativas) que el modelo usa como referencia de
formato, nivel de detalle y criterio a la hora de generar nuevas
estimaciones a partir de una transcripción de reunión.
"""

ESTIMATION_EXAMPLES = [
    {
        "meeting_summary": (
            "El cliente (una pyme de logística) necesita una plataforma web de "
            "gestión de inventario multi-almacén. Debe permitir dar de alta "
            "productos, controlar stock por almacén, registrar entradas/salidas, "
            "generar alertas de stock mínimo y ofrecer un dashboard con métricas "
            "de rotación. Necesitan roles diferenciados (administrador, "
            "almacenero, solo lectura para dirección). No requieren app móvil "
            "en esta fase, solo web responsive. Integración futura con su ERP "
            "actual (Odoo) pero no es parte de este alcance inicial."
        ),
        "estimation": """
## Estimación: Plataforma de Gestión de Inventario Multi-almacén

### Desglose de tareas:
1. Diseño UI/UX (wireframes + prototipo navegable): 40 horas
2. Backend API (CRUD productos, almacenes, movimientos de stock): 60 horas
3. Autenticación y roles (admin / almacenero / lectura): 20 horas
4. Motor de alertas de stock mínimo: 12 horas
5. Dashboard con métricas de rotación e histórico: 30 horas
6. Frontend responsive (consumo de API): 50 horas
7. Testing y QA (unitario + pruebas de integración): 25 horas
8. Despliegue y documentación técnica: 10 horas

**Total estimado: 247 horas**
**Equipo recomendado: 2 desarrolladores full-stack + 1 diseñador UX (part-time)**
**Duración estimada: 7-9 semanas**
**Riesgos/supuestos: no incluye integración con Odoo (fuera de alcance); se
asume que el cliente entrega el catálogo inicial de productos en formato
estructurado (CSV/Excel).**
        """,
    },
    {
        "meeting_summary": (
            "El cliente es una clínica dental con 3 sedes que quiere digitalizar "
            "la reserva de citas. Necesitan una app móvil (iOS y Android) para "
            "que los pacientes reserven, modifiquen y cancelen citas, con "
            "recordatorios push 24h antes. También un panel web interno para "
            "que recepción gestione la agenda de cada sede y de cada dentista. "
            "Quieren pago online opcional para reservar (señal de 10€) mediante "
            "Stripe. No hay sistema previo, se parte de cero. El diseño debe "
            "seguir su identidad de marca ya existente (tienen guía de estilo)."
        ),
        "estimation": """
## Estimación: App de Reserva de Citas + Panel de Gestión (Clínica Dental)

### Desglose de tareas:
1. Diseño UI/UX app + panel (a partir de guía de marca existente): 35 horas
2. Backend API (citas, sedes, profesionales, disponibilidad): 70 horas
3. App móvil multiplataforma (React Native): reserva/edición/cancelación: 90 horas
4. Notificaciones push (recordatorios 24h antes): 15 horas
5. Integración de pagos con Stripe (señal de reserva): 20 horas
6. Panel web interno de gestión de agenda multi-sede: 55 horas
7. Autenticación de pacientes y personal interno: 18 horas
8. Testing y QA (incluye pruebas en iOS/Android reales): 35 horas
9. Publicación en App Store / Google Play y despliegue backend: 12 horas

**Total estimado: 350 horas**
**Equipo recomendado: 1 desarrollador backend, 1 desarrollador mobile,
1 desarrollador frontend web, 1 diseñador UX (part-time)**
**Duración estimada: 10-12 semanas**
**Riesgos/supuestos: el proceso de revisión de las tiendas de apps puede
añadir 1-2 semanas no controlables por el equipo; se asume que las
credenciales de Stripe y las cuentas de desarrollador (Apple/Google) las
aporta el cliente.**
        """,
    },
    {
        "meeting_summary": (
            "El cliente tiene un e-commerce en Shopify y quiere automatizar la "
            "sincronización de pedidos con su sistema de facturación (Holded). "
            "Cuando se completa un pedido en Shopify, debe crearse "
            "automáticamente la factura correspondiente en Holded, con los "
            "impuestos correctos según el país del comprador. También quieren "
            "un log de sincronizaciones fallidas y un reintento manual desde un "
            "pequeño panel de administración. Volumen aproximado: 200-300 "
            "pedidos/día. No hay necesidad de interfaz para el cliente final, "
            "solo backend + panel interno básico."
        ),
        "estimation": """
## Estimación: Integración Automática Shopify → Holded (Facturación)

### Desglose de tareas:
1. Análisis de APIs (Shopify webhooks + API Holded) y diseño de la integración: 15 horas
2. Servicio de escucha de webhooks de pedidos completados: 20 horas
3. Lógica de mapeo pedido → factura (líneas, impuestos por país): 35 horas
4. Manejo de errores, reintentos y cola de sincronizaciones fallidas: 25 horas
5. Panel interno de administración (log + reintento manual): 30 horas
6. Autenticación básica del panel (usuario interno único rol): 8 horas
7. Testing (incluye simulación de picos ~300 pedidos/día): 20 horas
8. Despliegue, monitorización y documentación de la integración: 12 horas

**Total estimado: 165 horas**
**Equipo recomendado: 1 desarrollador backend senior + 1 desarrollador
backend/QA (part-time)**
**Duración estimada: 5-6 semanas**
**Riesgos/supuestos: se asume acceso de API con permisos suficientes en
ambas plataformas desde el inicio del proyecto; la lógica de impuestos se
basa en las reglas fiscales vigentes en el momento de la estimación y podría
requerir ajustes si cambian.**
        """,
    },
]
