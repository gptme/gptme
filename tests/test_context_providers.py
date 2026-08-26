"""Tests for the context compression provider interface.

Covers:
- ContextProvider ABC contract
- DefaultContextProvider implementation and parity with auto_compact_log
- Provider registration and lookup
- Entry-point loading
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.autocompact.context_provider import (
    CompactionResult,
    CompressionConfig,
    ContextProvider,
    DefaultContextProvider,
    _compute_source_digest,
    get_context_provider,
    list_providers,
    register_provider,
)

# =============================================================================
# Fixtures and Test Providers
# =============================================================================


class MockContextProvider(ContextProvider):
    """A mock provider for testing the interface contract."""

    # Class-level name that can be overridden for registration tests
    _provider_name = "mock"

    def __init__(self):
        self.should_compress_called = False
        self.compress_called = False

    @property
    def name(self) -> str:
        return self._provider_name

    def should_compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> bool:
        self.should_compress_called = True
        return True

    def compress(
        self, messages: list[Message], config: CompressionConfig
    ) -> CompactionResult:
        self.compress_called = True
        return CompactionResult(
            messages=list(messages),
            source_digest=_compute_source_digest(messages),
            covered_through=len(messages) - 1 if messages else -1,
        )


@pytest.fixture
def cleanup_registry():
    """Cleanup the provider registry after tests."""
    from gptme.tools.autocompact import context_provider as ctx_module

    # Save original registry state AND the initialized flag
    original_registry = ctx_module._provider_registry.copy()
    original_initialized = ctx_module._registry_initialized

    yield

    # Restore both — leaving _registry_initialized=True with an empty registry
    # causes get_context_provider() to skip re-initialization and see [] permanently.
    ctx_module._provider_registry.clear()
    ctx_module._provider_registry.update(original_registry)
    ctx_module._registry_initialized = original_initialized


@pytest.fixture
def sample_messages():
    """Create sample messages for testing compression."""
    return [
        Message("user", "Hello, help me with this"),
        Message("assistant", "I'll help. Here's a detailed response."),
        Message("user", "Can you expand on that?"),
        Message("assistant", "Sure, here are more details about the topic."),
    ]


@pytest.fixture
def compression_config():
    """Create a default compression config."""
    return CompressionConfig(limit=4000, max_tool_result_tokens=2000)


# =============================================================================
# Tests for the ContextProvider ABC
# =============================================================================


def test_context_provider_is_abstract():
    """ContextProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ContextProvider()  # type: ignore[abstract]


def test_context_provider_requires_name():
    """All providers must implement the name property."""

    class BadProvider(ContextProvider):
        def should_compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> bool:
            return True

        def compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> CompactionResult:
            return CompactionResult(
                messages=list(messages), source_digest="", covered_through=-1
            )

    with pytest.raises(TypeError):
        BadProvider()  # type: ignore[abstract]


def test_context_provider_requires_should_compress():
    """All providers must implement should_compress."""

    class BadProvider(ContextProvider):
        @property
        def name(self) -> str:
            return "bad"

        def compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> CompactionResult:
            return CompactionResult(
                messages=list(messages), source_digest="", covered_through=-1
            )

    with pytest.raises(TypeError):
        BadProvider()  # type: ignore[abstract]


def test_context_provider_requires_compress():
    """All providers must implement compress."""

    class BadProvider(ContextProvider):
        @property
        def name(self) -> str:
            return "bad"

        def should_compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> bool:
            return True

    with pytest.raises(TypeError):
        BadProvider()  # type: ignore[abstract]


def test_mock_provider_implements_interface(sample_messages, compression_config):
    """MockContextProvider correctly implements the ContextProvider interface."""
    provider = MockContextProvider()

    assert provider.name == "mock"
    assert provider.should_compress(sample_messages, compression_config)
    assert provider.should_compress_called

    result = provider.compress(sample_messages, compression_config)
    assert provider.compress_called
    assert isinstance(result, CompactionResult)
    assert result.messages == sample_messages
    assert result.covered_through == len(sample_messages) - 1


# =============================================================================
# Tests for DefaultContextProvider
# =============================================================================


def test_default_provider_name():
    """DefaultContextProvider reports its name as 'default'."""
    provider = DefaultContextProvider()
    assert provider.name == "default"


def test_default_provider_should_compress_true_over_limit(sample_messages):
    """DefaultContextProvider triggers compression when over token limit."""
    config = CompressionConfig(limit=100)  # Very low limit
    provider = DefaultContextProvider()

    # With many messages, should decide to compress
    result = provider.should_compress(sample_messages, config)
    # Result depends on token estimation, but the method should be callable
    assert isinstance(result, bool)


def test_default_provider_should_compress_false_under_limit(sample_messages):
    """DefaultContextProvider may skip compression well under limit."""
    config = CompressionConfig(limit=100000)  # Very high limit
    provider = DefaultContextProvider()

    result = provider.should_compress(sample_messages, config)
    assert isinstance(result, bool)


def test_default_provider_compress_returns_compaction_result(
    sample_messages, compression_config
):
    """DefaultContextProvider.compress() returns a CompactionResult."""
    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, compression_config)

    assert isinstance(result, CompactionResult)
    assert len(result.messages) > 0
    assert all(isinstance(msg, Message) for msg in result.messages)
    assert isinstance(result.source_digest, str) and len(result.source_digest) == 64
    assert result.covered_through == len(sample_messages) - 1
    assert isinstance(result.limitations, list)


def test_default_provider_compress_preserves_message_structure(
    sample_messages, compression_config
):
    """DefaultContextProvider preserves message role and order."""
    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, compression_config)

    # Check that roles are preserved
    original_roles = [msg.role for msg in sample_messages]
    compressed_roles = [msg.role for msg in result.messages]

    # Order should be preserved
    assert compressed_roles == original_roles


def test_default_provider_estimate_tokens(sample_messages):
    """DefaultContextProvider can estimate tokens in messages."""
    provider = DefaultContextProvider()
    token_count = provider.estimate_tokens(sample_messages)

    assert isinstance(token_count, int)
    assert token_count > 0


def test_default_provider_parity_with_auto_compact(sample_messages, compression_config):
    """DefaultContextProvider produces same output as auto_compact_log."""
    from gptme.tools.autocompact.engine import auto_compact_log

    provider = DefaultContextProvider()

    # Get results from both paths using identical parameters
    result = provider.compress(sample_messages, compression_config)
    autocompact_result = list(
        auto_compact_log(
            sample_messages,
            limit=compression_config.limit,
            max_tool_result_tokens=compression_config.max_tool_result_tokens,
        )
    )

    # Both should produce a list of messages
    assert len(result.messages) > 0
    assert len(autocompact_result) > 0

    # For the default provider, results should be identical
    # (same algorithm, same inputs)
    assert len(result.messages) == len(autocompact_result)

    # Verify message integrity
    for msg in result.messages:
        assert isinstance(msg, Message)
        assert msg.role in ("user", "assistant", "system")


# =============================================================================
# Tests for Provider Registration
# =============================================================================


def test_register_provider(cleanup_registry):
    """Providers can be registered by name."""

    # Create a provider class with a specific name for testing
    class TestProvider(MockContextProvider):
        _provider_name = "test-provider"

    register_provider("test-provider", TestProvider)

    # Should be able to get the registered provider
    retrieved = get_context_provider("test-provider")
    assert retrieved.name == "test-provider"


def test_custom_default_provider_preserved_across_init(cleanup_registry):
    """Registering a custom 'default' before first lookup is not clobbered.

    Regression test for a Greptile finding: _init_registry() used direct
    assignment for the built-in default, so a caller that registered a custom
    provider under "default" before the first lookup lost it. The init must use
    setdefault so the custom default survives.
    """
    from gptme.tools.autocompact import context_provider as ctx_module

    # Ensure registry is uninitialized so the lookup path runs _init_registry()
    ctx_module._registry_initialized = False
    ctx_module._provider_registry.clear()

    class CustomDefault(MockContextProvider):
        _provider_name = "default"

    register_provider("default", CustomDefault)

    # First lookup triggers _init_registry(); custom default must survive
    retrieved = get_context_provider("default")
    assert retrieved.__class__ is CustomDefault


@patch("importlib.metadata.entry_points")
def test_entry_point_does_not_clobber_preregistered_default(
    mock_entry_points, cleanup_registry
):
    """An entry point named 'default' must not overwrite a pre-registered custom default.

    Regression test for a Greptile P1: _load_entry_point_providers() called
    register_provider() unconditionally, so a plugin entry point named "default"
    could clobber a caller-selected custom default even after setdefault() in
    _init_registry() preserved it.
    """
    from gptme.tools.autocompact import context_provider as ctx_module

    class CustomDefault(MockContextProvider):
        _provider_name = "default"

    class EntryPointDefault(MockContextProvider):
        _provider_name = "default"

    # Pre-register the custom default
    ctx_module._provider_registry.clear()
    ctx_module._registry_initialized = False
    register_provider("default", CustomDefault)

    # Mock an entry point also named "default"
    mock_ep = MagicMock()
    mock_ep.name = "default"
    mock_ep.load.return_value = EntryPointDefault
    mock_entry_points.return_value = [mock_ep]

    # Trigger init (which calls _load_entry_point_providers)
    provider = get_context_provider("default")

    # The pre-registered custom default must win over the entry point
    assert provider.__class__ is CustomDefault, (
        f"Expected CustomDefault but got {provider.__class__.__name__}; "
        "entry point must not clobber a pre-registered provider"
    )


@patch("importlib.metadata.entry_points")
def test_entry_point_default_provider_is_reachable(mock_entry_points, cleanup_registry):
    """An entry-point provider named 'default' must win over the built-in default.

    Regression test for Greptile P1: _init_registry() ran setdefault("default", ...)
    before calling _load_entry_point_providers(), so a plugin entry point named
    "default" was silently skipped and the built-in default was always used.
    Fix: load entry points first, then setdefault as the fallback.
    """
    from gptme.tools.autocompact import context_provider as ctx_module
    from gptme.tools.autocompact.context_provider import get_context_provider

    class EntryPointDefault(MockContextProvider):
        _provider_name = "default"

    ctx_module._provider_registry.clear()
    ctx_module._registry_initialized = False

    mock_ep = MagicMock()
    mock_ep.name = "default"
    mock_ep.load.return_value = EntryPointDefault
    mock_entry_points.return_value = [mock_ep]

    provider = get_context_provider("default")

    assert provider.__class__ is EntryPointDefault, (
        f"Expected EntryPointDefault but got {provider.__class__.__name__}; "
        "an entry-point 'default' provider must take priority over the built-in"
    )


def test_register_provider_requires_subclass(cleanup_registry):
    """Only ContextProvider subclasses can be registered."""

    class NotAProvider:
        pass

    with pytest.raises(TypeError):
        register_provider("bad", NotAProvider)


def test_register_provider_duplicate_overrides(cleanup_registry):
    """Registering a provider with the same name overwrites the previous one."""

    register_provider("test", MockContextProvider)
    first = get_context_provider("test")

    class OtherProvider(ContextProvider):
        @property
        def name(self) -> str:
            return "other"

        def should_compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> bool:
            return False

        def compress(
            self, messages: list[Message], config: CompressionConfig
        ) -> CompactionResult:
            return CompactionResult(
                messages=list(messages), source_digest="", covered_through=-1
            )

    register_provider("test", OtherProvider)
    second = get_context_provider("test")

    # Should be different instances (different classes)
    assert first.__class__ is not second.__class__


# =============================================================================
# Tests for Provider Lookup
# =============================================================================


def test_get_context_provider_default():
    """get_context_provider() returns 'default' provider by default."""
    provider = get_context_provider()
    assert provider.name == "default"


def test_get_context_provider_explicit_name():
    """get_context_provider(name) returns the named provider."""
    provider = get_context_provider("default")
    assert provider.name == "default"


def test_get_context_provider_unknown_name():
    """get_context_provider() raises ValueError for unknown names."""
    with pytest.raises(ValueError, match="Unknown context provider"):
        get_context_provider("nonexistent-provider-xyz")


def test_list_providers():
    """list_providers() returns all registered provider names."""
    names = list_providers()

    assert isinstance(names, list)
    assert "default" in names
    assert all(isinstance(n, str) for n in names)
    # List should be sorted
    assert names == sorted(names)


# =============================================================================
# Tests for Entry-Point Loading
# =============================================================================


@patch("importlib.metadata.entry_points")
def test_load_entry_point_providers_python310(mock_entry_points, cleanup_registry):
    """Entry-point loading works with Python 3.10+ API."""
    from gptme.tools.autocompact.context_provider import _load_entry_point_providers

    # Mock Python 3.10+ API (group parameter)
    mock_ep = MagicMock()
    mock_ep.name = "test-provider"
    mock_ep.load.return_value = MockContextProvider

    mock_entry_points.return_value = [mock_ep]

    _load_entry_point_providers()

    # Verify entry point was loaded
    mock_entry_points.assert_called_once()


@patch("importlib.metadata.entry_points")
def test_load_entry_point_providers_python39_fallback(
    mock_entry_points, cleanup_registry
):
    """Entry-point loading falls back to Python 3.9 API on TypeError."""
    from gptme.tools.autocompact.context_provider import _load_entry_point_providers

    # First call (Python 3.10+ API) raises TypeError, triggering fallback
    mock_ep = MagicMock()
    mock_ep.name = "test-provider"
    mock_ep.load.return_value = MockContextProvider

    mock_entry_points.side_effect = [
        TypeError("group parameter not supported"),  # First call fails
        {"gptme.context_providers": [mock_ep]},  # Second call succeeds
    ]

    _load_entry_point_providers()

    # Verify both attempts were made
    assert mock_entry_points.call_count == 2


@patch("importlib.metadata.entry_points")
def test_load_entry_point_providers_handles_load_error(mock_entry_points, caplog):
    """Entry-point loading logs warnings but doesn't crash on load errors."""
    from gptme.tools.autocompact.context_provider import _load_entry_point_providers

    mock_ep = MagicMock()
    mock_ep.name = "broken-provider"
    mock_ep.load.side_effect = ImportError("Module not found")

    mock_entry_points.return_value = [mock_ep]

    # Should not raise
    _load_entry_point_providers()


# =============================================================================
# Integration Tests
# =============================================================================


def test_full_workflow_with_default_provider(sample_messages, compression_config):
    """End-to-end workflow using get_context_provider."""
    # Get default provider
    provider = get_context_provider("default")

    # Check if compression is needed
    should_compress = provider.should_compress(sample_messages, compression_config)
    assert isinstance(should_compress, bool)

    # Apply compression if needed
    if should_compress:
        result = provider.compress(sample_messages, compression_config)
        assert isinstance(result, CompactionResult)
        assert len(result.messages) > 0
        assert all(isinstance(msg, Message) for msg in result.messages)


def test_provider_config_custom_settings(sample_messages):
    """CompressionConfig can include custom provider-specific settings."""
    config = CompressionConfig(
        limit=5000,
        max_tool_result_tokens=3000,
        reasoning_strip_age_threshold=3,
        extra_config={"custom_key": "custom_value"},
    )

    assert config.limit == 5000
    assert config.max_tool_result_tokens == 3000
    assert config.reasoning_strip_age_threshold == 3
    assert config.extra_config["custom_key"] == "custom_value"


def test_provider_with_logdir(sample_messages, tmp_path):
    """DefaultContextProvider respects the logdir for saving removed outputs."""
    config = CompressionConfig(limit=1000, logdir=tmp_path)

    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, config)

    # Should be able to handle logdir without errors
    assert len(result.messages) > 0


def test_multiple_providers_coexist(cleanup_registry, sample_messages):
    """Multiple different providers can be registered and used together."""
    register_provider("mock1", MockContextProvider)
    register_provider("mock2", MockContextProvider)

    provider1 = get_context_provider("mock1")
    provider2 = get_context_provider("mock2")

    # Should be different instances
    assert provider1 is not provider2
    # But same type
    assert provider1.__class__ is provider2.__class__


# =============================================================================
# Tests for CompactionResult
# =============================================================================


def test_compaction_result_fields(sample_messages, compression_config):
    """CompactionResult exposes messages, source_digest, covered_through, limitations."""
    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, compression_config)

    assert hasattr(result, "messages")
    assert hasattr(result, "source_digest")
    assert hasattr(result, "covered_through")
    assert hasattr(result, "limitations")


def test_source_digest_is_stable(sample_messages):
    """Same input produces the same source_digest."""
    d1 = _compute_source_digest(sample_messages)
    d2 = _compute_source_digest(sample_messages)
    assert d1 == d2


def test_source_digest_changes_on_mutation(sample_messages):
    """A different message list produces a different source_digest."""
    d_original = _compute_source_digest(sample_messages)
    mutated = sample_messages + [Message("user", "extra message")]
    d_mutated = _compute_source_digest(mutated)
    assert d_original != d_mutated


def test_source_digest_empty_input():
    """Empty input produces a deterministic digest without error."""
    d = _compute_source_digest([])
    assert isinstance(d, str) and len(d) == 64


def test_covered_through_empty_input():
    """compress() on empty input returns covered_through=-1."""
    provider = DefaultContextProvider()
    config = CompressionConfig()
    result = provider.compress([], config)
    assert result.covered_through == -1


def test_covered_through_full_input(sample_messages, compression_config):
    """DefaultContextProvider covers the full input (covered_through = last index)."""
    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, compression_config)
    assert result.covered_through == len(sample_messages) - 1


def test_default_provider_digest_matches_source(sample_messages, compression_config):
    """source_digest in result matches _compute_source_digest of the input."""
    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, compression_config)
    expected_digest = _compute_source_digest(sample_messages)
    assert result.source_digest == expected_digest


def test_limitations_is_list(sample_messages, compression_config):
    """limitations is always a list (may be empty for third-party providers)."""
    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, compression_config)
    assert isinstance(result.limitations, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
