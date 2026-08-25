"""Context compression provider interface for gptme.

Defines the ContextProvider ABC and the built-in DefaultContextProvider,
plus the registry used to look up providers by name.
"""

from __future__ import annotations

import importlib.metadata
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from ...message import Message

logger = logging.getLogger(__name__)


@dataclass
class CompressionConfig:
    """Configuration for context compression."""

    limit: int | None = None
    max_tool_result_tokens: int = 2000
    logdir: Path | None = None
    reasoning_strip_age_threshold: int = 5
    keep_head: int = 0
    extra_config: dict = field(default_factory=dict)


class ContextProvider(ABC):
    """Abstract base class for context compression providers.

    Implementations must provide a ``name`` property, a ``should_compress``
    predicate, and a ``compress`` generator.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider name used for registration and lookup."""
        ...

    @abstractmethod
    def should_compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> bool:
        """Return True if these messages should be compressed."""
        ...

    @abstractmethod
    def compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> Generator[Message, None, None]:
        """Compress messages and yield the result."""
        ...


class DefaultContextProvider(ContextProvider):
    """Default provider — delegates to ``gptme.tools.autocompact.engine.auto_compact_log``."""

    @property
    def name(self) -> str:
        return "default"

    def should_compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> bool:
        if config.limit is None:
            return False
        return self.estimate_tokens(messages) > config.limit

    def compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> Generator[Message, None, None]:
        from .engine import auto_compact_log

        yield from auto_compact_log(
            messages,
            limit=config.limit,
            max_tool_result_tokens=config.max_tool_result_tokens,
            logdir=config.logdir,
            reasoning_strip_age_threshold=config.reasoning_strip_age_threshold,
            keep_head=config.keep_head,
        )

    def estimate_tokens(self, messages: list[Message]) -> int:
        """Estimate total token count for a message list."""
        from ...llm.models import get_default_model

        m = get_default_model()
        if m:
            from ...util.tokens import len_tokens

            return len_tokens(messages, f"{m.provider}/{m.model}")
        # Fallback: ~4 chars per token
        return sum(len(str(msg.content)) for msg in messages) // 4


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_provider_registry: dict[str, type[ContextProvider]] = {}
_registry_initialized = False


def _init_registry() -> None:
    global _registry_initialized
    if not _registry_initialized:
        _registry_initialized = True
        # Load entry-point plugins first so a plugin named "default" takes
        # precedence over the built-in.  setdefault below only fills the gap
        # when no plugin (and no pre-registered provider) claimed "default".
        _load_entry_point_providers()
        # setdefault (not assignment): respect both pre-registered providers
        # (registered before the first lookup) and entry-point "default" plugins.
        _provider_registry.setdefault("default", DefaultContextProvider)


def register_provider(name: str, provider_class: type) -> None:
    """Register a ContextProvider subclass under *name*."""
    if not (
        isinstance(provider_class, type) and issubclass(provider_class, ContextProvider)
    ):
        raise TypeError(
            f"provider_class must be a ContextProvider subclass, got {provider_class!r}"
        )
    _provider_registry[name] = provider_class


def get_context_provider(name: str = "default") -> ContextProvider:
    """Return an instance of the named provider.

    Raises :class:`ValueError` for unknown names.
    """
    _init_registry()
    if name not in _provider_registry:
        raise ValueError(
            f"Unknown context provider: {name!r}. "
            f"Available: {sorted(_provider_registry.keys())}"
        )
    return _provider_registry[name]()


def list_providers() -> list[str]:
    """Return all registered provider names, sorted."""
    _init_registry()
    return sorted(_provider_registry.keys())


def _load_entry_point_providers() -> None:
    """Load providers registered via the ``gptme.context_providers`` entry-point group."""
    try:
        eps = importlib.metadata.entry_points(group="gptme.context_providers")
    except TypeError:
        # Python 3.9 fallback: entry_points() returns a dict-like object
        try:
            all_eps: Any = importlib.metadata.entry_points()
            eps = all_eps.get("gptme.context_providers", [])
        except Exception:
            logger.warning("Failed to enumerate entry points for context providers")
            return

    for ep in eps:
        try:
            provider_class = ep.load()
            # setdefault semantics: a pre-registered provider (including one
            # explicitly set under "default") must not be clobbered by an
            # entry point of the same name.
            if ep.name not in _provider_registry:
                register_provider(ep.name, provider_class)
        except Exception as exc:
            logger.warning(
                "Failed to load context provider entry point %r: %s", ep.name, exc
            )
