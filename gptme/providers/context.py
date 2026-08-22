"""Context compression provider interface.

Allows third-party packages to implement custom context compression strategies
as pluggable providers, registered via entry-points.

This module defines the ContextProvider ABC that all compression strategies
must implement, plus the built-in DefaultContextProvider that wraps the
existing auto_compact_log algorithm.

Entry-point registration:
    # In setup.py or pyproject.toml:
    [project.entry-points."gptme.context_providers"]
    my_provider = "my_package.providers:MyContextProvider"

Example usage:
    >>> from gptme.providers.context import get_context_provider
    >>> provider = get_context_provider("my_provider")
    >>> if provider.should_compress(context_len=5000, limit=4000):
    ...     compacted = provider.compress(messages)
"""

from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..message import Message


@dataclass
class CompressionConfig:
    """Configuration for context compression strategies.

    Attributes:
        limit: Target token limit for compressed context (None = use model default)
        max_tool_result_tokens: Maximum tokens allowed in a tool result before removal
        reasoning_strip_age_threshold: Strip reasoning from messages >N positions back
        logdir: Path to conversation directory for saving removed outputs (for recovery)
    """

    limit: int | None = None
    max_tool_result_tokens: int = 2000
    reasoning_strip_age_threshold: int = 5
    logdir: Path | None = None
    extra_config: dict[str, Any] | None = None  # For provider-specific settings


class ContextProvider(ABC):
    """Abstract base class for context compression providers.

    Implementations should provide a compression strategy that can decide whether
    compression is needed and apply the compression algorithm.

    This interface enables third-party packages to ship custom compression strategies
    while preserving backward compatibility with the built-in algorithm.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a unique identifier for this provider.

        Examples:
            - "default" for the built-in autocompact algorithm
            - "paritok-statistical" for a statistical compression provider
            - "opencode" for an open-source code-aware compressor

        Returns:
            str: Provider name, lowercase with hyphens (e.g., "my-provider")
        """

    @abstractmethod
    def should_compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> bool:
        """Decide whether compression should be applied to this message list.

        This allows providers to implement smart decisions: some may always compress
        when close to the limit, others may only compress if detected redundancy
        exceeds a threshold.

        Args:
            messages: List of messages in the conversation
            config: Compression configuration including token limit

        Returns:
            bool: True if compression should be applied, False otherwise
        """

    @abstractmethod
    def compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> Generator[Message, None, None]:
        """Apply context compression and yield reduced messages.

        Implementations should:
        1. Decide on a compression strategy based on the messages and config
        2. Apply transformations (remove, truncate, summarize, extract)
        3. Preserve message structure (role, timestamp, tool_calls, etc.)
        4. Yield compacted messages in order
        5. Log summary statistics about compression

        For recovery, implementations MAY add references to the master context
        (conversation.jsonl byte ranges) in message content when truncating,
        allowing exact recovery of removed content.

        Args:
            messages: List of messages to compress
            config: Compression configuration

        Yields:
            Message: Compacted messages in original order

        Note:
            Generators should be preferred over returning full lists to allow
            streaming processing of large conversations.
        """

    def estimate_tokens(self, messages: list[Message]) -> int:
        """Estimate total tokens in message list (can be overridden for efficiency).

        Default implementation uses the model's tokenizer.

        Args:
            messages: List of messages

        Returns:
            int: Estimated token count
        """
        from ..llm.models import get_default_model, get_model
        from ..message import len_tokens

        model = get_default_model() or get_model("gpt-4")
        return len_tokens(messages, model=model.model)


class DefaultContextProvider(ContextProvider):
    """Built-in context compression provider.

    Wraps the existing auto_compact_log algorithm from gptme.tools.autocompact.engine
    to maintain backward compatibility while enabling pluggable alternatives.

    This is the default provider used when no custom provider is registered.

    Compression strategy (3-phase):
    1. Strip reasoning tags from older messages (age-based)
    2. Truncate largest tool results first (oh-my-opencode strategy)
    3. Extractive compression for long assistant messages

    The algorithm intelligently prioritizes the largest messages for removal
    to achieve target reduction with minimal information loss.
    """

    @property
    def name(self) -> str:
        return "default"

    def should_compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> bool:
        """Decide if compression is needed based on token count and limit.

        Returns True if:
        - Token count exceeds the target limit, OR
        - Token count is within 70% of the limit (close to overflow)

        This allows preemptive compression before hitting hard limits.

        Args:
            messages: List of messages
            config: Compression configuration with token limit

        Returns:
            bool: True if compression should be applied
        """
        from ..llm.models import get_default_model, get_model

        model = get_default_model() or get_model("gpt-4")
        limit = config.limit or int(0.8 * model.context)

        tokens = self.estimate_tokens(messages)
        close_to_limit = tokens >= int(0.7 * model.context)

        return tokens > limit or close_to_limit

    def compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> Generator[Message, None, None]:
        """Apply auto_compact_log algorithm and yield compacted messages.

        Args:
            messages: List of messages to compress
            config: Compression configuration

        Yields:
            Message: Compacted messages in original order
        """
        from ..tools.autocompact.engine import auto_compact_log

        yield from auto_compact_log(
            messages,
            limit=config.limit,
            max_tool_result_tokens=config.max_tool_result_tokens,
            reasoning_strip_age_threshold=config.reasoning_strip_age_threshold,
            logdir=config.logdir,
        )


# Global provider registry (populated at import time from entry-points)
_provider_registry: dict[str, type[ContextProvider]] = {}


def register_provider(name: str, provider_class: type[ContextProvider]) -> None:
    """Register a context provider class.

    Args:
        name: Unique provider identifier (e.g., "default", "my-compressor")
        provider_class: Class implementing ContextProvider ABC
    """
    if not issubclass(provider_class, ContextProvider):
        raise TypeError(f"{provider_class} must be a ContextProvider subclass")
    _provider_registry[name] = provider_class


def get_context_provider(name: str = "default") -> ContextProvider:
    """Get a context provider instance by name.

    Args:
        name: Provider name (defaults to "default")

    Returns:
        ContextProvider: Instance of the requested provider

    Raises:
        ValueError: If provider name not found
    """
    if name not in _provider_registry:
        # Try to lazy-load from entry-points
        _load_entry_point_providers()

    if name not in _provider_registry:
        available = ", ".join(sorted(_provider_registry.keys()))
        raise ValueError(f"Unknown context provider '{name}'. Available: {available}")

    return _provider_registry[name]()


def list_providers() -> list[str]:
    """List all registered context provider names.

    Returns:
        list[str]: Sorted list of provider names
    """
    _load_entry_point_providers()
    return sorted(_provider_registry.keys())


def _load_entry_point_providers() -> None:
    """Load context providers from entry-points (importlib.metadata).

    Entry-point group: "gptme.context_providers"

    This is called lazily on first provider lookup to avoid import overhead
    when no custom providers are used.

    Note: gptme requires Python 3.10+, so we use the modern entry_points() API.
    """
    import importlib.metadata
    import logging

    logger = logging.getLogger(__name__)

    entry_points = importlib.metadata.entry_points(group="gptme.context_providers")

    for ep in entry_points:
        try:
            provider_class = ep.load()
            register_provider(ep.name, provider_class)
            logger.debug(f"Loaded context provider: {ep.name}")
        except Exception as e:
            logger.warning(f"Failed to load context provider {ep.name}: {e}")


# Register the built-in default provider
register_provider("default", DefaultContextProvider)
