"""Extractor de project_metadata con el LLM (implementa MetadataExtractorPort).

- Salida estructurada (response_model=ProjectMetadata): el proveedor recibe
  el schema y la respuesta se valida con ProjectMetadata (extra="forbid",
  límites de longitud). Nada sin validar entra en la sesión.
- Temperatura 0, max_tokens acotado y el timeout del wrapper; tier fast.
- Solo extrae: el merge con el metadata anterior (merge_metadata) y la
  política ante fallos los aplica el caso de uso (MemoryMaintainer).
"""

from app.conversation.domain.metadata import ProjectMetadata
from app.ports.llm import ModelTier, StructuredCompletion, StructuredLLM
from app.prompts.loader import render_metadata_extractor_prompt


class LLMMetadataExtractor:
    def __init__(self, llm: StructuredLLM, *, max_tokens: int, prompt_version: str = "v1") -> None:
        self._llm = llm
        self._max_tokens = max_tokens
        self._prompt_version = prompt_version

    def extract_with_usage(
        self, current: ProjectMetadata, user_message: str, assistant_message: str
    ) -> StructuredCompletion[ProjectMetadata]:
        system, user = render_metadata_extractor_prompt(
            current=current,
            user_message=user_message,
            assistant_message=assistant_message,
            version=self._prompt_version,
        )
        return self._llm.complete_structured_with_usage(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            ProjectMetadata,
            temperature=0,
            max_tokens=self._max_tokens,
            tier=ModelTier.FAST,
        )
