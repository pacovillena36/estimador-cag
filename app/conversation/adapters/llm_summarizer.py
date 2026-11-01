"""Summarizer acumulativo con el LLM (implementa ConversationSummarizer).

Una llamada barata (tier fast, temperatura 0) por cada turno que sale de la
ventana: resumen anterior + turno expulsado -> resumen nuevo, validado
contra ConversationSummary (límite de caracteres).
"""

from app.conversation.domain.memory import Turn
from app.conversation.ports import ConversationSummary
from app.ports.llm import ModelTier, StructuredCompletion, StructuredLLM
from app.prompts.loader import render_summary_prompt


class LLMConversationSummarizer:
    def __init__(self, llm: StructuredLLM, *, max_tokens: int, prompt_version: str = "v1") -> None:
        self._llm = llm
        self._max_tokens = max_tokens
        self._prompt_version = prompt_version

    def summarize(self, previous_summary: str, evicted: Turn) -> StructuredCompletion[ConversationSummary]:
        system, user = render_summary_prompt(
            previous_summary=previous_summary,
            user_message=evicted.user_content,
            assistant_message=evicted.assistant_content,
            version=self._prompt_version,
        )
        return self._llm.complete_structured_with_usage(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            ConversationSummary,
            temperature=0,
            max_tokens=self._max_tokens,
            tier=ModelTier.FAST,
        )
