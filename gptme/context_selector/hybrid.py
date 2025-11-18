"""Hybrid context selection combining rule-based and LLM-based approaches."""

import logging
from collections.abc import Sequence

from .base import ContextItem, ContextSelector
from .config import ContextSelectorConfig
from .llm_based import LLMSelector
from .rule_based import RuleBasedSelector

logger = logging.getLogger(__name__)


class HybridSelector(ContextSelector):
    """Combine rule-based pre-filtering with LLM refinement.

    Strategy:
    1. Fast keyword pre-filter to reduce candidate set (e.g., 100 -> 20)
    2. If pre-filtered set is small enough, return it (no LLM call needed)
    3. Otherwise, use LLM to refine pre-filtered set (e.g., 20 -> 5)

    Benefits:
    - Fast: Most requests avoid LLM call
    - Accurate: LLM refinement when needed
    - Cost-effective: LLM only evaluates pre-filtered candidates
    """

    def __init__(self, config: ContextSelectorConfig):
        self.config = config
        self.rule_selector = RuleBasedSelector(config)
        self.llm_selector = LLMSelector(config)

    async def select(
        self,
        query: str,
        candidates: Sequence[ContextItem],
        max_results: int = 5,
    ) -> list[ContextItem]:
        """Select using hybrid approach: rule pre-filter + LLM refinement."""

        # Phase 1: Rule-based pre-filter
        max_candidates = self.config.max_candidates
        pre_filtered = await self.rule_selector.select(
            query=query,
            candidates=candidates,
            max_results=max_candidates,
        )

        logger.debug(
            f"HybridSelector: Pre-filtered {len(candidates)} -> {len(pre_filtered)} candidates"
        )

        # Phase 2: LLM refinement (if needed)
        if len(pre_filtered) <= max_results:
            # Pre-filtered set is already small enough
            logger.debug("HybridSelector: Skipping LLM refinement (small enough)")
            return pre_filtered

        # Use LLM to refine the pre-filtered set
        logger.debug(
            f"HybridSelector: LLM refining {len(pre_filtered)} -> {max_results}"
        )
        refined = await self.llm_selector.select(
            query=query,
            candidates=pre_filtered,
            max_results=max_results,
        )

        return refined
