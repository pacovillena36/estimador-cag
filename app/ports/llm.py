"""Puerto de salida hacia el LLM: lo único que conoce el núcleo conversacional.

El núcleo (app/conversation) depende de este Protocol, no de LiteLLM: el
adaptador real es LLMGateway (app/services/llm_gateway.py) y en los tests se
usa un doble en memoria.

El tier es abstracto ("fast" / "strong"): qué modelo concreto hay detrás lo
decide el adaptador con la configuración, nunca el núcleo.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class ModelTier(StrEnum):
    FAST = "fast"
    STRONG = "strong"


@dataclass(frozen=True)
class LLMUsage:
    """Observaciones de una llamada al LLM. `cost_usd` es None si el modelo
    no tiene precio conocido (desconocido no es lo mismo que 0)."""

    model: str
    provider: str
    latency_ms: float
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    cached: bool = False


@dataclass(frozen=True)
class StructuredCompletion(Generic[M]):
    """Instancia validada de `response_model` junto con su uso."""

    parsed: M
    usage: LLMUsage


class StructuredLLM(Protocol):
    def complete_structured_with_usage(
        self,
        messages: list[dict],
        response_model: type[M],
        *,
        context: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tier: ModelTier = ModelTier.FAST,
    ) -> StructuredCompletion[M]: ...
