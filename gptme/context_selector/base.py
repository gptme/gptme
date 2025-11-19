"""Base abstractions for context selection.

This module provides the core interfaces for selecting relevant context items
using different strategies (rule-based, LLM-based, hybrid).
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass
class ContextItem(ABC):
    """Base class for items that can be selected as context.

    Subclasses should implement content, metadata, and identifier properties
    to provide the necessary information for selection strategies.
    """

    @property
    @abstractmethod
    def content(self) -> str:
        """Return the text content for LLM evaluation."""
        pass

    @property
    @abstractmethod
    def metadata(self) -> dict[str, Any]:
        """Return metadata (YAML frontmatter, file stats, etc.)."""
        pass

    @property
    @abstractmethod
    def identifier(self) -> str:
        """Return unique identifier for this item."""
        pass


class ContextSelector(ABC):
    """Base class for context selection strategies.

    Different implementations can use rule-based matching, LLM evaluation,
    embeddings, or hybrid approaches to select the most relevant items.
    """

    @abstractmethod
    async def select(
        self,
        query: str,
        candidates: Sequence[ContextItem],
        max_results: int = 5,
    ) -> list[ContextItem]:
        """Select the most relevant items from candidates.

        Args:
            query: The user message or context to match against
            candidates: List of potential items to select from
            max_results: Maximum number of items to return

        Returns:
            List of selected items, ordered by relevance
        """
        pass
