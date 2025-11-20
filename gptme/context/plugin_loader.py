"""Plugin loader and registry for context management plugins."""

from typing import Any

from .plugin import ContextPlugin, TransformResult


class PluginRegistry:
    """Registry for context management plugins.

    Manages plugin discovery, registration, and loading.
    """

    def __init__(self):
        """Initialize plugin registry."""
        self._plugins: dict[str, ContextPlugin] = {}

    def register(self, plugin: ContextPlugin) -> None:
        """Register a plugin.

        Args:
            plugin: Plugin instance to register
        """
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> ContextPlugin | None:
        """Get plugin by name.

        Args:
            name: Plugin name

        Returns:
            Plugin instance or None if not found
        """
        return self._plugins.get(name)

    def list(self) -> list[str]:
        """List registered plugin names.

        Returns:
            List of plugin names
        """
        return list(self._plugins.keys())

    def transform(
        self, plugin_name: str, content: str, context: dict[str, Any]
    ) -> TransformResult:
        """Transform content using specified plugin.

        Args:
            plugin_name: Name of plugin to use
            content: Content to transform
            context: Context for transformation

        Returns:
            TransformResult from plugin

        Raises:
            ValueError: If plugin not found
        """
        plugin = self.get(plugin_name)
        if plugin is None:
            raise ValueError(f"Plugin not found: {plugin_name}")
        return plugin.transform(content, context)


class PluginPipeline:
    """Pipeline for chaining multiple plugins.

    Executes plugins in sequence, passing output of one to the next.
    """

    def __init__(self, plugins: list[ContextPlugin]):
        """Initialize plugin pipeline.

        Args:
            plugins: List of plugins to execute in order
        """
        self.plugins = plugins

    def transform(self, content: str, context: dict[str, Any]) -> TransformResult:
        """Transform content through plugin pipeline.

        Args:
            content: Content to transform
            context: Context for transformation

        Returns:
            Final TransformResult after all plugins
        """
        current_content = content
        total_tokens_saved = 0
        metadata_chain = []

        for plugin in self.plugins:
            result = plugin.transform(current_content, context)
            current_content = result.content
            total_tokens_saved += result.tokens_saved
            metadata_chain.append(
                {
                    "plugin": plugin.name,
                    "tokens_saved": result.tokens_saved,
                    "metadata": result.metadata,
                }
            )

        return TransformResult(
            content=current_content,
            tokens_saved=total_tokens_saved,
            metadata={"pipeline": metadata_chain},
        )


# Global plugin registry
_default_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    """Get the default plugin registry.

    Returns:
        Default PluginRegistry instance
    """
    return _default_registry


def register_plugin(plugin: ContextPlugin) -> None:
    """Register a plugin in the default registry.

    Args:
        plugin: Plugin to register
    """
    _default_registry.register(plugin)
