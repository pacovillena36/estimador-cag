"""Memoria conversacional en tres capas.

1. Ventana deslizante (ConversationHistory): los últimos `max_turns` pares
   completos user+assistant, que viajan literales como mensajes.
2. Anclas: frases clave de los turnos que ya salieron de la ventana.
3. Resumen acumulativo: el hilo de la conversación expulsada, en prosa.

Las anclas de un turno se calculan al guardarlo, pero solo se promueven a la
capa 2 cuando el turno sale de la ventana: mientras está dentro, su texto ya
viaja literal y repetirlo sería gastar tokens. El resumen lo genera un
puerto (ConversationSummarizer); aquí solo se guarda.
"""

from collections import deque
from dataclasses import dataclass

from app.conversation.domain.anchors import Anchor


@dataclass(frozen=True)
class Turn:
    """Un par completo: nunca se guarda un mensaje de usuario sin respuesta."""

    number: int
    user_content: str
    assistant_content: str
    anchors: tuple[Anchor, ...] = ()


class ConversationHistory:
    """Pares (user, assistant) en una ventana deslizante de `max_turns`.

    El system prompt NO vive aquí: se regenera en cada llamada a partir del
    estado actual, así que siempre va en la posición 0 y nunca se descarta
    al deslizar la ventana.
    """

    def __init__(self, max_turns: int) -> None:
        self._turns: deque[Turn] = deque()
        self._max_turns = max_turns
        self._recorded = 0

    def __len__(self) -> int:
        return len(self._turns)

    @property
    def recorded(self) -> int:
        """Turnos guardados desde el inicio (incluidos los expulsados)."""
        return self._recorded

    def add_turn(
        self, user_content: str, assistant_content: str, anchors: tuple[Anchor, ...] = ()
    ) -> Turn | None:
        """Guarda el par y devuelve el turno expulsado de la ventana, si lo hay."""
        self._recorded += 1
        self._turns.append(Turn(self._recorded, user_content, assistant_content, anchors))
        return self._turns.popleft() if len(self._turns) > self._max_turns else None

    def to_messages_list(self, system_prompt: str) -> list[dict[str, str]]:
        """System prompt en primera posición y después los pares en orden
        cronológico. Si el proveedor quiere el system aparte (Anthropic),
        lo separa el cliente LLM (LiteLLM), no esta clase."""
        messages = [{"role": "system", "content": system_prompt}]
        for turn in self._turns:
            messages.append({"role": "user", "content": turn.user_content})
            messages.append({"role": "assistant", "content": turn.assistant_content})
        return messages


class ConversationMemory:
    def __init__(self, max_turns: int, max_anchors: int) -> None:
        self.history = ConversationHistory(max_turns)
        self._max_anchors = max_anchors
        self._anchors: list[Anchor] = []
        self.summary = ""

    def __len__(self) -> int:
        return len(self.history)

    @property
    def anchors(self) -> tuple[Anchor, ...]:
        return tuple(self._anchors)

    @property
    def message_count(self) -> int:
        """Mensajes de historial que viajan al LLM (sin system ni turno actual)."""
        return 2 * len(self.history)

    @property
    def next_turn_number(self) -> int:
        return self.history.recorded + 1

    def record_turn(
        self, user_content: str, assistant_content: str, anchors: tuple[Anchor, ...]
    ) -> Turn | None:
        """Guarda el turno; si otro sale de la ventana, promueve sus anclas y
        lo devuelve para que se resuma."""
        evicted = self.history.add_turn(user_content, assistant_content, anchors)
        if evicted is not None:
            self._promote(evicted.anchors)
        return evicted

    def update_summary(self, summary: str) -> None:
        self.summary = summary.strip()

    def to_messages(self, system_prompt: str) -> list[dict[str, str]]:
        return self.history.to_messages_list(system_prompt)

    def _promote(self, anchors: tuple[Anchor, ...]) -> None:
        keys = {a.key for a in self._anchors}
        for anchor in anchors:
            if anchor.key not in keys:
                keys.add(anchor.key)
                self._anchors.append(anchor)
        # Por encima del máximo se descartan las más antiguas.
        del self._anchors[: max(0, len(self._anchors) - self._max_anchors)]
