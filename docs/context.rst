====================
Context Compression
====================

gptme provides a pluggable context compression system that allows conversations to be compacted when they grow too large. This enables long-running sessions while keeping context windows manageable.

.. contents:: Table of Contents
   :local:
   :depth: 2

Overview
========

The context compression system has two main components:

1. **Automatic Compaction** - Triggered when conversations exceed token limits or contain massive tool results
2. **Plugin Interface** - Allows third-party packages to provide custom compression strategies

Built-in Compression Strategy
==============================

By default, gptme uses a 3-phase compression algorithm:

1. **Reasoning Stripping** - Remove reasoning tags from older messages (age-based)
2. **Tool Result Truncation** - Truncate largest tool results first
3. **Extractive Compression** - Summarize long assistant messages

This approach intelligently prioritizes the largest messages for removal to achieve target reduction with minimal information loss.

Using the Default Compressor
=============================

When you enable the ``autocompact`` tool, automatic compression is triggered via a post-turn hook:

.. code-block:: bash

    gptme --tool autocompact

You can also manually compact a conversation:

.. code-block:: text

    /compact auto      # Rule-based compaction
    /compact resume    # LLM-powered resume generation

Custom Compression Providers (Plugin Interface)
===============================================

The plugin interface allows third-party packages to ship custom compression strategies as pip packages. This is useful for:

- Domain-specific compaction strategies (e.g., code-aware, markdown-aware)
- Experimental compression algorithms
- Integration with external summarization services
- Specialized handling for specific tool outputs

Implementing a Custom Provider
==============================

Create a class that implements the ``ContextProvider`` abstract base class:

.. code-block:: python

    from gptme.providers.context import ContextProvider, CompressionConfig
    from collections.abc import Generator
    from gptme.message import Message

    class MyContextProvider(ContextProvider):
        """Custom context compression provider."""

        @property
        def name(self) -> str:
            """Return a unique identifier for this provider."""
            return "my-compressor"

        def should_compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> bool:
            """Decide whether compression should be applied."""
            # Your logic here
            return len(messages) > 50

        def compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> Generator[Message, None, None]:
            """Apply compression and yield compacted messages."""
            # Your compression logic here
            yield from messages

Provider Interface
==================

ContextProvider ABC
-------------------

All custom providers must inherit from ``gptme.providers.context.ContextProvider`` and implement:

``name`` property
~~~~~~~~~~~~~~~~~

Returns a unique identifier for this provider (e.g., ``"my-compressor"``).

.. code-block:: python

    @property
    def name(self) -> str:
        return "my-compressor"

``should_compress()`` method
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Decides whether compression should be applied based on message list and configuration.

.. code-block:: python

    def should_compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> bool:
        """
        Args:
            messages: List of messages in the conversation
            config: Compression configuration (see CompressionConfig below)

        Returns:
            True if compression should be applied, False otherwise
        """

This allows intelligent decisions: some providers might always compress when close to the limit, others only when detecting redundancy exceeds a threshold.

``compress()`` method
~~~~~~~~~~~~~~~~~~~~~

Applies compression and yields reduced messages.

.. code-block:: python

    def compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> Generator[Message, None, None]:
        """
        Args:
            messages: List of messages to compress
            config: Compression configuration

        Yields:
            Message: Compacted messages in original order

        Note:
            - Preserve message structure (role, timestamp, tool_calls, etc.)
            - Log summary statistics about compression
            - May optionally add references to master context for recovery
        """

CompressionConfig Dataclass
----------------------------

Configuration passed to compression methods:

.. code-block:: python

    @dataclass
    class CompressionConfig:
        limit: int | None = None
            # Target token limit (None = use model default)

        max_tool_result_tokens: int = 2000
            # Maximum tokens allowed in a tool result before removal

        reasoning_strip_age_threshold: int = 5
            # Strip reasoning from messages older than N positions

        logdir: Path | None = None
            # Path to save removed outputs for recovery

        extra_config: dict[str, Any] | None = None
            # Provider-specific configuration

Registering Your Provider
===========================

Entry Points (Recommended)
--------------------------

Add your provider to your package's ``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."gptme.context_providers"]
    my-compressor = "my_package.providers:MyContextProvider"

When gptme starts, it will automatically discover and load your provider via entry-points.

Programmatic Registration
--------------------------

You can also register providers at runtime:

.. code-block:: python

    from gptme.providers.context import register_provider
    from my_package.providers import MyContextProvider

    register_provider("my-compressor", MyContextProvider)

Using a Custom Provider
=======================

Once registered, use your provider by name:

.. code-block:: python

    from gptme.providers.context import get_context_provider

    provider = get_context_provider("my-compressor")
    config = CompressionConfig(limit=4000)

    # Check if compression is needed
    if provider.should_compress(messages, config):
        compacted = list(provider.compress(messages, config))

Discovering Available Providers
-------------------------------

List all registered providers:

.. code-block:: python

    from gptme.providers.context import list_providers

    providers = list_providers()
    # Returns: ['default', 'my-compressor', ...]

Design Considerations
=====================

Message Integrity
-----------------

Compression implementations must preserve:

- **Message Role**: Keep assistant/user/system roles intact
- **Message Order**: Maintain original chronological order
- **Timestamps**: Don't modify message timestamps
- **Tool References**: Preserve tool_use_id and tool_result associations

This ensures the compacted conversation remains valid for continuation.

Recovery Strategy
-----------------

For significant compression, consider adding recovery references:

.. code-block:: python

    # When removing or summarizing messages, optionally record:
    # - Byte ranges in the original conversation.jsonl
    # - A `sourceDigest` hash for verification
    # - Coverage metadata (how many events/turns were covered)

This allows recovery of the full context if needed later.

Performance
-----------

Generators are preferred over returning full lists:

.. code-block:: python

    # ✅ Preferred: streaming processing
    def compress(self, messages, config) -> Generator[Message, None, None]:
        yield from messages

    # ❌ Avoid: allocating full list
    def compress(self, messages, config) -> list[Message]:
        return messages

Streaming allows processing large conversations without allocating full lists.

Examples
========

Code-Aware Compression
----------------------

Example provider that's more aggressive with code comments:

.. code-block:: python

    class CodeAwareProvider(ContextProvider):
        @property
        def name(self) -> str:
            return "code-aware"

        def should_compress(self, messages, config) -> bool:
            # Only compress when close to limit
            tokens = self.estimate_tokens(messages)
            limit = config.limit or 4000
            return tokens > int(0.9 * limit)

        def compress(self, messages, config):
            for msg in messages:
                if msg.role == "assistant" and "```" in msg.content:
                    # Truncate code comments more aggressively
                    yield self._compress_code_message(msg, config)
                else:
                    yield msg

Statistical Compression
-----------------------

Provider that uses redundancy detection:

.. code-block:: python

    class StatisticalProvider(ContextProvider):
        @property
        def name(self) -> str:
            return "statistical"

        def should_compress(self, messages, config) -> bool:
            # Analyze redundancy before deciding
            redundancy = self._compute_redundancy_score(messages)
            return redundancy > 0.3  # 30% redundant content

        def compress(self, messages, config):
            # Remove duplicate patterns, extract key information
            yield from self._extract_unique_content(messages)

Related
=======

- ``autocompact`` tool - Automatic compression tool (see :doc:`commands`)
- ``gptme.providers.context`` - Provider interface module
- :py:class:`gptme.message.Message` - Message class reference

API Reference
=============

.. autoclass:: gptme.providers.context.ContextProvider
   :members:
   :undoc-members:

.. autoclass:: gptme.providers.context.DefaultContextProvider
   :members:
   :undoc-members:

.. autoclass:: gptme.providers.context.CompressionConfig
   :members:
   :undoc-members:

.. autofunction:: gptme.providers.context.get_context_provider

.. autofunction:: gptme.providers.context.register_provider

.. autofunction:: gptme.providers.context.list_providers
