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

    /compact           # Rule-based trim (default)
    /compact trim      # Same as above
    /compact summarize # LLM-powered summarization

Two strategies are available:

- **trim** (default) — rule-based: strips old reasoning blocks, truncates massive tool
  results, and compresses long assistant messages. Fast and deterministic; no LLM call.
  If savings would be low, gptme will suggest ``/compact summarize`` instead.

- **summarize** — LLM-powered: asks the model to produce a ``RESUME.md`` capturing
  key decisions, open tasks, and relevant file paths, then starts a fresh context
  from that summary. More thorough but requires a model call and restarts context.

.. deprecated::
   ``/compact auto`` and ``/compact resume`` are deprecated aliases for ``trim`` and
   ``summarize`` respectively. They still work but emit a deprecation warning.

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

    from gptme.tools.autocompact.context_provider import (
        ContextProvider, CompressionConfig, CompactionResult,
    )
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
        ) -> CompactionResult:
            """Apply compression and return a CompactionResult."""
            # Your compression logic here — return a CompactionResult, not a generator
            source_digest = self._compute_digest(messages)
            compacted = messages[-50:]  # keep last 50 messages
            return CompactionResult(
                messages=compacted,
                source_digest=source_digest,
            )

Provider Interface
==================

ContextProvider ABC
-------------------

All custom providers must inherit from ``gptme.tools.autocompact.context_provider.ContextProvider`` and implement:

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

Applies compression and returns a :class:`~gptme.tools.autocompact.context_provider.CompactionResult`.

.. code-block:: python

    def compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> CompactionResult:
        """
        Args:
            messages: List of messages to compress
            config: Compression configuration

        Returns:
            CompactionResult: Contains the compacted message list and
                metadata (source_digest) for cache invalidation.

        Note:
            - Preserve message structure (role, timestamp, tool_calls, etc.)
            - Do NOT return a generator — the caller accesses .messages directly
            - May optionally set covered_through for partial-context tracking
        """

CompressionConfig Dataclass
----------------------------

Configuration passed to compression methods:

.. code-block:: python

    @dataclass
    class CompressionConfig:
        limit: int | None = None
            # Target token limit. None disables automatic should_compress()
            # checks (DefaultContextProvider.should_compress returns False).
            # Custom providers may handle None differently.

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

    from gptme.tools.autocompact.context_provider import register_provider
    from my_package.providers import MyContextProvider

    register_provider("my-compressor", MyContextProvider)

Using a Custom Provider
=======================

Once registered, use your provider by name:

.. code-block:: python

    from gptme.tools.autocompact.context_provider import get_context_provider

    provider = get_context_provider("my-compressor")
    config = CompressionConfig(limit=4000)

    # Check if compression is needed
    if provider.should_compress(messages, config):
        result = provider.compress(messages, config)
        compacted = result.messages  # CompactionResult.messages is a list[Message]

Discovering Available Providers
-------------------------------

List all registered providers:

.. code-block:: python

    from gptme.tools.autocompact.context_provider import list_providers

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

The ``compress`` method must return a :class:`~gptme.tools.autocompact.context_provider.CompactionResult`
(not a generator or a bare list). When building the result, process messages
lazily to avoid allocating unnecessary copies:

.. code-block:: python

    def compress(self, messages, config) -> CompactionResult:
        # Build compacted list efficiently with a comprehension or generator expression
        compacted = [self._maybe_trim(m) for m in messages]
        return CompactionResult(
            messages=compacted,
            source_digest=self._compute_digest(messages),
        )

Examples
========

Code-Aware Compression
----------------------

Example provider that's more aggressive with code comments:

.. code-block:: python

    from gptme.tools.autocompact.context_provider import (
        ContextProvider, CompressionConfig, CompactionResult,
    )

    class CodeAwareProvider(ContextProvider):
        @property
        def name(self) -> str:
            return "code-aware"

        def should_compress(self, messages, config) -> bool:
            # Only compress when close to limit
            tokens = self.estimate_tokens(messages)
            limit = config.limit or 4000
            return tokens > int(0.9 * limit)

        def compress(self, messages, config) -> CompactionResult:
            compacted = [
                self._compress_code_message(msg, config)
                if msg.role == "assistant" and "```" in msg.content
                else msg
                for msg in messages
            ]
            return CompactionResult(
                messages=compacted,
                source_digest=self._compute_digest(messages),
            )

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

        def compress(self, messages, config) -> CompactionResult:
            # Remove duplicate patterns, extract key information
            compacted = list(self._extract_unique_content(messages))
            return CompactionResult(
                messages=compacted,
                source_digest=self._compute_digest(messages),
            )

Related
=======

- ``autocompact`` tool - Automatic compression tool (see :doc:`commands`)
- ``gptme.tools.autocompact.context_provider`` - Provider interface module
- :py:class:`gptme.message.Message` - Message class reference

API Reference
=============

.. autoclass:: gptme.tools.autocompact.context_provider.ContextProvider
   :members:
   :undoc-members:

.. autoclass:: gptme.tools.autocompact.context_provider.DefaultContextProvider
   :members:
   :undoc-members:

.. autoclass:: gptme.tools.autocompact.context_provider.CompactionResult
   :members:
   :undoc-members:

.. autoclass:: gptme.tools.autocompact.context_provider.CompressionConfig
   :members:
   :undoc-members:

.. autofunction:: gptme.tools.autocompact.context_provider.get_context_provider

.. autofunction:: gptme.tools.autocompact.context_provider.register_provider

.. autofunction:: gptme.tools.autocompact.context_provider.list_providers
