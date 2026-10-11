"""Escenarios sintéticos multi-turno del stress test del CAG.

Tres conversaciones de 20 turnos; los checkpoints N ∈ {1, 3, 6, 10, 20} se
evalúan sobre la misma conversación (no son conversaciones distintas).

- growing: el turno 1 fija un nombre de proyecto poco común ("Nimbus") y
  cada turno añade un requisito coherente. Curva de coste y supervivencia
  del nombre al turno 20.
- pivot: stack React en los turnos 1-4; el turno 5 cambia a Flutter de forma
  explícita. ¿Acumula mentioned_technologies ambas?
- contradiction: presupuesto de 30000 EUR en el turno 3 y de 80000 EUR en el
  turno 8. ¿Cuál sobrevive en resumen, anclas y metadata?

Contenido 100 % ficticio y determinista (sin aleatoriedad). Los facts son
cadenas cortas que se buscan LITERALMENTE (sin mayúsculas ni espacios
extra): si el sistema reescribe "80000" como "80.000 €", la métrica lo
cuenta como fallo y así se reporta; no se relaja el match.
"""

from dataclasses import dataclass
from typing import Literal

ScenarioName = Literal["growing", "pivot", "contradiction"]
CHECKPOINTS: tuple[int, ...] = (1, 3, 6, 10, 20)
TURNS_PER_SCENARIO = 20


@dataclass(frozen=True)
class Turn:
    turn_index: int  # 1-based
    transcript: str
    fact_to_remember: str | None  # lo que deberían recordar los turnos posteriores
    stale_fact: str | None = None  # fact que este turno sustituye (contradicción / pivote)


@dataclass(frozen=True)
class Scenario:
    name: ScenarioName
    project_type: str
    turns: tuple[Turn, ...]  # 20 turnos

    def turn(self, turn_index: int) -> Turn:
        return self.turns[turn_index - 1]


@dataclass(frozen=True)
class TrackedFact:
    turn_index: int  # turno en el que se fijó
    fact: str


def expected_facts(scenario: Scenario, n: int) -> tuple[TrackedFact, ...]:
    """Facts vigentes que el turno N debería recordar: los fijados en turnos
    k < N y no sustituidos por un turno <= N."""
    stale = {t.stale_fact.casefold() for t in scenario.turns[:n] if t.stale_fact}
    return tuple(
        TrackedFact(t.turn_index, t.fact_to_remember)
        for t in scenario.turns[: n - 1]
        if t.fact_to_remember and t.fact_to_remember.casefold() not in stale
    )


def stale_facts(scenario: Scenario, n: int) -> tuple[str, ...]:
    """Facts ya sustituidos en algún turno <= N (no deberían seguir vigentes)."""
    return tuple(t.stale_fact for t in scenario.turns[:n] if t.stale_fact)


def _build(name: ScenarioName, project_type: str, rows: list[tuple[str, str | None, str | None]]) -> Scenario:
    if len(rows) != TURNS_PER_SCENARIO:
        raise ValueError(f"{name}: {len(rows)} turnos (se esperan {TURNS_PER_SCENARIO})")
    turns = tuple(Turn(i, transcript, fact, stale) for i, (transcript, fact, stale) in enumerate(rows, start=1))
    return Scenario(name, project_type, turns)


GROWING = _build(
    "growing",
    "web_saas",
    [
        (
            "Queremos estimar el proyecto Nimbus: una plataforma web SaaS para que pequeñas "
            "academias gestionen alumnos, grupos y matrículas.",
            "Nimbus",
            None,
        ),
        ("Es requisito obligatorio la autenticación con email y contraseña y recuperación de contraseña.", "autenticación", None),
        ("La plataforma debe ser multi-tenant: cada academia ve solo sus datos.", "multi-tenant", None),
        ("Necesitamos un audit log de todas las acciones de los administradores.", "audit log", None),
        ("Los listados de alumnos deben permitir export CSV.", "export CSV", None),
        ("Las academias grandes piden SSO con su proveedor de identidad corporativo.", "SSO", None),
        ("Hay que enviar notificaciones por email cuando cambia el horario de un grupo.", "notificaciones", None),
        ("Queremos un calendario semanal de clases por aula y por profesor.", "calendario", None),
        ("Los pagos de las matrículas se cobrarán con pasarela de pago y recibos en PDF.", "pasarela de pago", None),
        ("Necesitamos roles y permisos: administrador, profesor y secretaría.", "roles y permisos", None),
        ("El control de asistencia se marcará desde el móvil del profesor.", "control de asistencia", None),
        ("Añadimos un panel de métricas con ocupación de grupos e ingresos mensuales.", "panel de métricas", None),
        ("La interfaz debe estar disponible en español, inglés y catalán.", "catalán", None),
        ("Hace falta una API pública REST para que las academias integren su web.", "API pública", None),
        ("Queremos importar alumnos desde una hoja de cálculo existente.", "importar alumnos", None),
        ("Los profesores podrán subir material didáctico a cada grupo.", "material didáctico", None),
        ("Hay que guardar copias de seguridad diarias con retención de 30 días.", "copias de seguridad", None),
        ("Necesitamos una lista de espera automática cuando un grupo se llena.", "lista de espera", None),
        ("Las familias tendrán un portal de solo lectura con notas y asistencia.", "portal de familias", None),
        (
            "Para cerrar: resume el alcance completo del proyecto Nimbus y dame la estimación final.",
            None,
            None,
        ),
    ],
)

PIVOT = _build(
    "pivot",
    "mobile_app",
    [
        (
            "Queremos estimar el proyecto Brisamar: una app de reservas de pistas de pádel para "
            "un club ficticio. Usaremos React para el frontend web del club.",
            "Brisamar",
            None,
        ),
        ("Los socios reservan pista, pagan y reciben confirmación.", "confirmación", None),
        ("El panel de React mostrará la ocupación diaria de las pistas.", None, None),
        ("Con React también haremos la gestión de socios y tarifas.", None, None),
        (
            "Cambio de planes: descartamos React por completo. Hemos decidido usar Flutter para una "
            "app móvil nativa en iOS y Android, sin frontend web.",
            "Flutter",
            "React",
        ),
        ("La app Flutter tendrá notificaciones push de recordatorio de reserva.", "notificaciones push", None),
        ("Los socios podrán invitar a otros jugadores a su reserva.", "invitar", None),
        ("Necesitamos un ranking interno de jugadores por nivel.", "ranking", None),
        ("Hay que integrar el control de acceso por QR en la puerta de las pistas.", "QR", None),
        ("Los monitores publicarán clases grupales con plazas limitadas.", "clases grupales", None),
        ("Queremos modo offline para consultar las reservas sin conexión.", "modo offline", None),
        ("Las tarifas cambian en hora punta y fin de semana.", "hora punta", None),
        ("El club quiere estadísticas de uso por pista y franja horaria.", "estadísticas", None),
        ("Añadimos un chat entre los jugadores de una misma reserva.", "chat", None),
        ("Hay que permitir cancelar con devolución hasta 24 horas antes.", "devolución", None),
        ("El backend expondrá una API para la app y un panel de administración.", "panel de administración", None),
        ("Los torneos internos tendrán cuadro de eliminatorias.", "torneos", None),
        ("Necesitamos accesibilidad con tamaños de letra grandes.", "accesibilidad", None),
        ("Las fotos de perfil se moderarán antes de publicarse.", "fotos de perfil", None),
        (
            "Para cerrar: confirma el stack definitivo del proyecto Brisamar y dame la estimación final.",
            None,
            None,
        ),
    ],
)

CONTRADICTION = _build(
    "contradiction",
    "internal_tool",
    [
        (
            "Queremos estimar el proyecto Kestrel: una herramienta interna para gestionar el "
            "inventario de equipos informáticos de una empresa ficticia.",
            "Kestrel",
            None,
        ),
        ("Cada equipo tendrá ficha con número de serie, ubicación y responsable.", "número de serie", None),
        ("El presupuesto máximo es de 30000 EUR.", "30000", None),
        ("Necesitamos asignar y devolver equipos con firma del empleado.", "firma", None),
        ("Hay que avisar cuando una garantía esté a punto de caducar.", "garantía", None),
        ("Queremos lectura de etiquetas con código de barras.", "código de barras", None),
        ("El inventario se sincroniza con el directorio de empleados.", "directorio de empleados", None),
        (
            "Corrección importante: el presupuesto máximo ya no es 30000 EUR; dirección lo ha "
            "ampliado a 80000 EUR.",
            "80000",
            "30000",
        ),
        ("Con el nuevo presupuesto añadimos un módulo de incidencias.", "incidencias", None),
        ("Queremos informes trimestrales de amortización.", "amortización", None),
        ("Las bajas de equipos requieren aprobación del responsable de IT.", "aprobación", None),
        ("Hay que registrar el historial de reparaciones de cada equipo.", "reparaciones", None),
        ("Añadimos inventario de licencias de software.", "licencias", None),
        ("La herramienta debe funcionar en la intranet sin acceso público.", "intranet", None),
        ("Queremos alertas de stock mínimo de periféricos.", "stock mínimo", None),
        ("Las auditorías anuales necesitan exportar el inventario completo.", "auditorías", None),
        ("Los empleados podrán solicitar equipos desde un formulario.", "solicitar equipos", None),
        ("Necesitamos un mapa de ubicaciones por planta.", "mapa de ubicaciones", None),
        ("El acceso será con la cuenta corporativa de cada empleado.", "cuenta corporativa", None),
        (
            "Para cerrar: confirma el presupuesto vigente del proyecto Kestrel y dame la estimación final.",
            None,
            None,
        ),
    ],
)

SCENARIOS: dict[ScenarioName, Scenario] = {s.name: s for s in (GROWING, PIVOT, CONTRADICTION)}
