"""Tests for context plugin system."""

import pytest

from gptme.context.plugin import ContextPlugin, TransformResult
from gptme.context.plugins import CompressionPlugin


def test_transform_result_creation():
    """Test TransformResult creation."""
    result = TransformResult(
        content="test content",
        tokens_saved=10,
        metadata={"method": "test"},
    )
    assert result.content == "test content"
    assert result.tokens_saved == 10
    assert result.metadata == {"method": "test"}


def test_transform_result_defaults():
    """Test TransformResult default values."""
    result = TransformResult(content="test")
    assert result.content == "test"
    assert result.tokens_saved == 0
    assert result.metadata == {}


def test_context_plugin_is_abstract():
    """Test that ContextPlugin cannot be instantiated."""
    with pytest.raises(TypeError):
        ContextPlugin()  # type: ignore


def test_compression_plugin_name():
    """Test CompressionPlugin name property."""
    plugin = CompressionPlugin()
    assert plugin.name == "compression"


def test_compression_plugin_transform_basic():
    """Test CompressionPlugin transforms content."""
    plugin = CompressionPlugin()
    content = "Hello world"
    result = plugin.transform(content, {})

    assert isinstance(result, TransformResult)
    assert result.content == "Hello world"
    assert result.tokens_saved == 0


def test_compression_plugin_strips_think_tags():
    """Test CompressionPlugin strips <think> tags."""
    plugin = CompressionPlugin()
    content = "Hello <think>reasoning here</think> world"
    result = plugin.transform(content, {"model": "gpt-4"})

    assert "<think>" not in result.content
    assert "</think>" not in result.content
    assert "Hello" in result.content
    assert "world" in result.content
    assert result.tokens_saved > 0


def test_compression_plugin_strips_thinking_tags():
    """Test CompressionPlugin strips <thinking> tags."""
    plugin = CompressionPlugin()
    content = "Hello <thinking>reasoning here</thinking> world"
    result = plugin.transform(content, {"model": "gpt-4"})

    assert "<thinking>" not in result.content
    assert "</thinking>" not in result.content
    assert "Hello" in result.content
    assert "world" in result.content
    assert result.tokens_saved > 0


def test_compression_plugin_config_schema():
    """Test CompressionPlugin configuration schema."""
    plugin = CompressionPlugin()
    schema = plugin.get_config_schema()

    assert isinstance(schema, dict)
    assert "properties" in schema
    assert "model" in schema["properties"]


def test_compression_plugin_validate_config_valid():
    """Test CompressionPlugin config validation with valid config."""
    plugin = CompressionPlugin()
    assert plugin.validate_config({"model": "gpt-4"})
    assert plugin.validate_config({})


def test_compression_plugin_validate_config_invalid():
    """Test CompressionPlugin config validation with invalid config."""
    plugin = CompressionPlugin()
    assert not plugin.validate_config({"model": 123})
