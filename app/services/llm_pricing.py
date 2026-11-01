"""Tabla de precios de los modelos (USD por millón de tokens).

Fuente principal del coste de cada llamada. Se mantiene a mano porque el
mapa de costes que viaja con LiteLLM no siempre incluye los modelos más
recientes. Si un modelo no está aquí, el wrapper recurre a LiteLLM y, si
tampoco lo conoce, el coste queda como None (desconocido, no 0).

Precios de API de primera parte (Anthropic / OpenAI). Revisarlos al cambiar
de modelo: un precio desactualizado falsea todas las métricas de coste.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


MODEL_COSTS: dict[str, ModelPrice] = {
    # Anthropic
    "claude-haiku-4-5": ModelPrice(1.00, 5.00),
    "claude-haiku-4-5-20251001": ModelPrice(1.00, 5.00),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00),
    # OpenAI
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    "gpt-4o": ModelPrice(2.50, 10.00),
}


def _bare_model(model: str) -> str:
    # "anthropic/claude-haiku-4-5" -> "claude-haiku-4-5"
    return model.rsplit("/", 1)[-1]


def price_for(model: str) -> ModelPrice | None:
    return MODEL_COSTS.get(_bare_model(model))


def cost_from_table(model: str, tokens_in: int, tokens_out: int) -> float | None:
    price = price_for(model)
    if price is None:
        return None
    cost = (tokens_in * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1_000_000
    return round(cost, 6)
