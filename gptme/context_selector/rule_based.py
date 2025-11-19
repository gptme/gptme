"""Rule-based context selection using keyword/pattern matching."""

import logging
from collections.abc import Sequence

from .base import ContextItem, ContextSelector
from .config import ContextSelectorConfig

logger = logging.getLogger(__name__)


class RuleBasedSelector(ContextSelector):
    """Select items using keyword and pattern matching.

    This is the traditional approach: match keywords in the query against
    keywords in item metadata. Fast, deterministic, zero cost.
    """

    def __init__(self, config: ContextSelectorConfig):
        self.config = config

    async def select(
        self,
        query: str,
        candidates: Sequence[ContextItem],
        max_results: int = 5,
    ) -> list[ContextItem]:
        """Select items by keyword matching."""
        query_lower = query.lower()
        scored_items: list[tuple[float, ContextItem, list[str]]] = []

        for item in candidates:
            score = 0.0
            matched_by = []
            metadata = item.metadata

            # Extract keywords from metadata
            keywords = metadata.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [keywords]

            # Match keywords
            for keyword in keywords:
                if isinstance(keyword, str) and keyword.lower() in query_lower:
                    score += 1.0
                    matched_by.append(f"keyword:{keyword}")

            # Priority boost (for lessons/tasks)
            priority = metadata.get("priority")
            if priority and self.config.lesson_use_yaml_metadata:
                boost = self.config.lesson_priority_boost.get(priority, 1.0)
                if boost > 1.0:
                    score *= boost
                    matched_by.append(f"priority:{priority}")

            # Tool matching (for lessons)
            tools = metadata.get("tools", [])
            if tools and "tool:" in query_lower:
                for tool in tools:
                    if f"tool:{tool}" in query_lower or tool in query_lower:
                        score += 2.0
                        matched_by.append(f"tool:{tool}")

            if score > 0:
                scored_items.append((score, item, matched_by))

        # Sort by score (descending)
        scored_items.sort(key=lambda x: x[0], reverse=True)

        # Return top N items
        selected = [item for score, item, matched_by in scored_items[:max_results]]

        logger.debug(
            f"RuleBasedSelector: {len(selected)}/{len(candidates)} items selected"
        )

        return selected
