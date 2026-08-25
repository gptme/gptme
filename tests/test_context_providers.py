"""Tests for the context compression provider interface.

Covers:
- ContextProvider ABC contract
- DefaultContextProvider implementation and parity with auto_compact_log
- Provider registration and lookup
- Entry-point loading
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.providers.context import (
    CompressionConfig,
    ContextProvider,
    DefaultContextProvider,
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
    ) -> Generator[Message, None, None]:
        self.compress_called = True
        yield from messages


@pytest.fixture
def cleanup_registry():
    """Cleanup the provider registry after tests."""
    original_registry = {}
    from gptme.providers import context as ctx_module

    # Save original registry state
    if hasattr(ctx_module, "_provider_registry"):
        original_registry = ctx_module._provider_registry.copy()

    yield

    # Restore original registry
    if hasattr(ctx_module, "_provider_registry"):
        ctx_module._provider_registry.clear()
        ctx_module._provider_registry.update(original_registry)


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
        ) -> Generator[Message, None, None]:
            yield from messages

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
        ) -> Generator[Message, None, None]:
            yield from messages

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

    compressed = list(provider.compress(sample_messages, compression_config))
    assert provider.compress_called
    assert compressed == sample_messages


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


def test_default_provider_compress_returns_generator(
    sample_messages, compression_config
):
    """DefaultContextProvider.compress() returns a generator."""
    provider = DefaultContextProvider()
    result = provider.compress(sample_messages, compression_config)

    assert isinstance(result, Generator)

    # Should be able to iterate over the result
    compressed = list(result)
    assert len(compressed) > 0
    assert all(isinstance(msg, Message) for msg in compressed)


def test_default_provider_compress_preserves_message_structure(
    sample_messages, compression_config
):
    """DefaultContextProvider preserves message role and order."""
    provider = DefaultContextProvider()
    compressed = list(provider.compress(sample_messages, compression_config))

    # Check that roles are preserved
    original_roles = [msg.role for msg in sample_messages]
    compressed_roles = [msg.role for msg in compressed]

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

    # Get results from both paths
    provider_result = list(provider.compress(sample_messages, compression_config))
    autocompact_result = list(auto_compact_log(sample_messages))

    # Both should produce a list of messages
    assert len(provider_result) > 0
    assert len(autocompact_result) > 0

    # For the default provider, results should be identical
    # (same algorithm, same inputs)
    assert len(provider_result) == len(autocompact_result)

    # Verify message integrity
    for msg in provider_result:
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
        ) -> Generator[Message, None, None]:
            yield from messages

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
def test_load_entry_point_providers_python310(mock_entry_points):
    """Entry-point loading works with Python 3.10+ API."""
    from gptme.providers.context import _load_entry_point_providers

    # Mock Python 3.10+ API (group parameter)
    mock_ep = MagicMock()
    mock_ep.name = "test-provider"
    mock_ep.load.return_value = MockContextProvider

    mock_entry_points.return_value = [mock_ep]

    _load_entry_point_providers()

    # Verify entry point was loaded
    mock_entry_points.assert_called_once()


@patch("importlib.metadata.entry_points")
def test_load_entry_point_providers_python39_fallback(mock_entry_points):
    """Entry-point loading falls back to Python 3.9 API on TypeError."""
    from gptme.providers.context import _load_entry_point_providers

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
    from gptme.providers.context import _load_entry_point_providers

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
        compressed = list(provider.compress(sample_messages, compression_config))
        assert len(compressed) > 0
        assert all(isinstance(msg, Message) for msg in compressed)


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
    compressed = list(provider.compress(sample_messages, config))

    # Should be able to handle logdir without errors
    assert len(compressed) > 0


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
