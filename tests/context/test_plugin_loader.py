"""Tests for plugin loader and registry."""

import pytest

from gptme.context.plugin import TransformResult
from gptme.context.plugin_loader import (
    PluginPipeline,
    PluginRegistry,
    get_registry,
    register_plugin,
)
from gptme.context.plugins import CompressionPlugin


def test_registry_register_and_get():
    """Test registering and getting plugins."""
    registry = PluginRegistry()
    plugin = CompressionPlugin()

    registry.register(plugin)
    retrieved = registry.get("compression")

    assert retrieved is plugin


def test_registry_get_nonexistent():
    """Test getting nonexistent plugin returns None."""
    registry = PluginRegistry()
    assert registry.get("nonexistent") is None


def test_registry_list_empty():
    """Test listing plugins when registry is empty."""
    registry = PluginRegistry()
    assert registry.list() == []


def test_registry_list_plugins():
    """Test listing registered plugins."""
    registry = PluginRegistry()
    plugin = CompressionPlugin()
    registry.register(plugin)

    plugins = registry.list()
    assert "compression" in plugins


def test_registry_transform():
    """Test transforming content via registry."""
    registry = PluginRegistry()
    plugin = CompressionPlugin()
    registry.register(plugin)

    content = "Hello <think>test</think> world"
    result = registry.transform("compression", content, {"model": "gpt-4"})

    assert isinstance(result, TransformResult)
    assert "<think>" not in result.content


def test_registry_transform_not_found():
    """Test transform raises ValueError for unknown plugin."""
    registry = PluginRegistry()

    with pytest.raises(ValueError, match="Plugin not found"):
        registry.transform("nonexistent", "test", {})


def test_pipeline_single_plugin():
    """Test pipeline with single plugin."""
    plugin = CompressionPlugin()
    pipeline = PluginPipeline([plugin])

    content = "Hello <think>test</think> world"
    result = pipeline.transform(content, {"model": "gpt-4"})

    assert isinstance(result, TransformResult)
    assert "<think>" not in result.content


def test_pipeline_multiple_plugins():
    """Test pipeline with multiple plugins."""
    plugin1 = CompressionPlugin()
    plugin2 = CompressionPlugin()
    pipeline = PluginPipeline([plugin1, plugin2])

    content = "Hello <think>test</think> world"
    result = pipeline.transform(content, {"model": "gpt-4"})

    assert isinstance(result, TransformResult)
    assert "pipeline" in result.metadata
    assert len(result.metadata["pipeline"]) == 2


def test_get_default_registry():
    """Test getting default registry."""
    registry = get_registry()
    assert isinstance(registry, PluginRegistry)


def test_register_plugin_default():
    """Test registering plugin in default registry."""
    plugin = CompressionPlugin()
    register_plugin(plugin)

    registry = get_registry()
    assert registry.get("compression") is not None
