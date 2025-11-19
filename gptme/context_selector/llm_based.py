"""LLM-based context selection using semantic understanding."""

import logging
import time
from collections.abc import Sequence

from ..llm import Message, reply
from .base import ContextItem, ContextSelector
from .config import ContextSelectorConfig

logger = logging.getLogger(__name__)


DEFAULT_SELECTION_PROMPT = """
You are an intelligent context selection assistant. Your task is to analyze candidate items and select the most relevant ones for a given query.

You will receive:
1. A user query or message inside <query> tags
2. A list of candidate items inside <candidates> tags, each with:
   - identifier: unique ID for the item
   - content: text content or description
   - metadata: additional information (keywords, priority, etc.)

Your task:
1. Analyze the query to understand what context would be most helpful
2. Evaluate each candidate for relevance to the query
3. Select the top candidates that would best help address the query
4. Output ONLY the identifiers of selected items, one per line

Output format:
<selected>
identifier1
identifier2
identifier3
</selected>

Important:
- Select based on semantic relevance, not just keyword matching
- Consider metadata (priority, tools, etc.) when available
- Output ONLY identifiers, no explanations or summaries
- If no candidates are relevant, output: <selected></selected>
"""


class LLMSelector(ContextSelector):
    """Select items using LLM semantic understanding.

    Uses a second LLM call to semantically evaluate candidates and select
    the most relevant ones. Based on the RAG post-processing pattern.
    """

    def __init__(self, config: ContextSelectorConfig):
        self.config = config
        self.prompt = DEFAULT_SELECTION_PROMPT

    async def select(
        self,
        query: str,
        candidates: Sequence[ContextItem],
        max_results: int = 5,
    ) -> list[ContextItem]:
        """Select items using LLM evaluation."""
        if not candidates:
            return []

        # Format candidates for LLM
        candidates_text = self._format_candidates(candidates)

        # Construct messages for LLM
        messages = [
            Message(role="system", content=self.prompt),
            Message(role="system", content=candidates_text),
            Message(
                role="user",
                content=f"<query>\n{query}\n</query>\n\nSelect up to {max_results} most relevant items.",
            ),
        ]

        # Call LLM
        start = time.monotonic()
        response = reply(
            messages=messages,
            model=self.config.llm_model,
        )
        duration = time.monotonic() - start

        logger.debug(f"LLM selection took {duration:.2f}s")

        # Parse response to extract selected identifiers
        selected_ids = self._parse_response(response.content)

        # Map identifiers back to items
        id_to_item = {item.identifier: item for item in candidates}
        selected = [id_to_item[id_] for id_ in selected_ids if id_ in id_to_item]

        logger.debug(f"LLMSelector: {len(selected)}/{len(candidates)} items selected")

        return selected[:max_results]

    def _format_candidates(self, candidates: Sequence[ContextItem]) -> str:
        """Format candidates for LLM evaluation."""
        lines = ["<candidates>"]

        for item in candidates:
            lines.append(f'\n<candidate id="{item.identifier}">')
            lines.append(f"<content>\n{item.content[:500]}\n</content>")

            metadata = item.metadata
            if metadata:
                lines.append("<metadata>")
                for key, value in metadata.items():
                    if key in ("keywords", "tools", "priority", "tags"):
                        lines.append(f"  {key}: {value}")
                lines.append("</metadata>")

            lines.append("</candidate>")

        lines.append("\n</candidates>")
        return "\n".join(lines)

    def _parse_response(self, response: str) -> list[str]:
        """Extract selected identifiers from LLM response."""
        identifiers = []

        # Look for <selected>...</selected> tags
        if "<selected>" in response and "</selected>" in response:
            start = response.index("<selected>") + len("<selected>")
            end = response.index("</selected>", start)  # Find closing tag after opening
            content = response[start:end].strip()

            if content:
                identifiers = [
                    line.strip() for line in content.split("\n") if line.strip()
                ]

        return identifiers
