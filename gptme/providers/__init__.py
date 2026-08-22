"""Pluggable provider interfaces for gptme.

This module provides abstract base classes that allow third-party packages
to extend gptme with custom implementations:

- **ContextProvider**: Custom context compression strategies
- **LogManager**: Custom log storage backends (future)
- **LLMProvider**: Custom LLM inference backends (future)

Each provider type has a registry and a discovery mechanism via entry-points.

Example: Custom Context Compression Provider
    # my_package/providers.py
    from gptme.providers.context import ContextProvider, CompressionConfig
    from gptme.message import Message

    class MyCompressor(ContextProvider):
        @property
        def name(self) -> str:
            return "my-compressor"

        def should_compress(self, messages, config):
            # Custom logic
            return len(messages) > 50

        def compress(self, messages, config):
            # Custom compression algorithm
            yield from apply_my_strategy(messages)

    # pyproject.toml
    [project.entry-points."gptme.context_providers"]
    my_compressor = "my_package.providers:MyCompressor"
"""

from .context import (
    CompressionConfig,
    ContextProvider,
    DefaultContextProvider,
    get_context_provider,
    list_providers,
    register_provider,
)

__all__ = [
    "CompressionConfig",
    "ContextProvider",
    "DefaultContextProvider",
    "get_context_provider",
    "list_providers",
    "register_provider",
]
