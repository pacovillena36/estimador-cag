"""Chunker estructural para presupuestos JSON: un componente = un chunk.

Cada chunk lleva delante el contexto del presupuesto padre (proyecto,
sector, año, tecnología principal): sin él, un componente "Authentication
backend" pierde a qué cliente y sector pertenece (contextual chunk
headers). No hay overlap ni troceado por tamaño: se confía en la
estructura del JSON y los chunks grandes solo se señalan en los logs.
"""

import structlog
import tiktoken

from app.embedding_pipeline.schemas import Budget, BudgetComponent, Chunk

log = structlog.get_logger(__name__)

# Umbral orientativo para señalar un componente anormalmente grande (no se
# trocea; es material de análisis).
LARGE_CHUNK_TOKENS = 1000

# Tipo de los chunks que produce este chunker (columna chunks.chunk_type).
CHUNK_TYPE = "budget_component"


class JSONStructuralChunker:
    def __init__(self, model: str) -> None:
        # Mismo tokenizador que el modelo de embeddings (token_count).
        self._encoding = tiktoken.encoding_for_model(model)

    def chunk(self, budgets: list[Budget]) -> list[Chunk]:
        chunks = []
        for budget in budgets:
            for component in budget.components:
                text = self._render(budget, component)
                chunk = Chunk(
                    chunk_id=f"{budget.budget_id}::{component.component_id}",
                    text=text,
                    metadata=self._metadata(budget, component),
                    token_count=len(self._encoding.encode(text)),
                )
                if chunk.token_count > LARGE_CHUNK_TOKENS:
                    log.warning(
                        "embeddings.large_chunk",
                        chunk_id=chunk.chunk_id,
                        token_count=chunk.token_count,
                    )
                chunks.append(chunk)
        return chunks

    @staticmethod
    def _render(budget: Budget, component: BudgetComponent) -> str:
        return (
            f"[Project: {budget.project_summary}]\n"
            f"[Client sector: {budget.client_metadata.sector} | Year: {budget.year}"
            f" | Main tech: {budget.main_technology}]\n"
            "\n"
            f"Component: {component.name}\n"
            f"Description: {component.description}\n"
            f"Tech stack: {', '.join(component.tech_stack)}\n"
            f"Complexity: {component.complexity}\n"
            f"Estimated hours: {component.estimated_hours:g}"
        )

    @staticmethod
    def _metadata(budget: Budget, component: BudgetComponent) -> dict[str, str | int | float]:
        return {
            "budget_id": budget.budget_id,
            "component_id": component.component_id,
            "client_sector": budget.client_metadata.sector,
            "main_technology": budget.main_technology,
            "year": budget.year,
            "complexity": component.complexity,
            "estimated_hours": component.estimated_hours,
        }
