# Context Management Plugin System

The context plugin system provides an extensible architecture for transforming and managing conversation context in gptme.

## Core Concepts

### ContextPlugin

Abstract base class for all context management plugins. Plugins implement the `transform()` method to modify context content.

```python
from gptme.context import ContextPlugin, TransformResult

class MyPlugin(ContextPlugin):
    @property
    def name(self) -> str:
        return "my-plugin"

    def transform(self, content: str, context: dict) -> TransformResult:
        # Transform content
        transformed = content.upper()
        return TransformResult(
            content=transformed,
            tokens_saved=0,
            metadata={"method": "uppercase"}
        )
```

### PluginRegistry

Central registry for discovering and loading plugins.

```python
from gptme.context import PluginRegistry, CompressionPlugin

# Create registry
registry = PluginRegistry()

# Register plugins
registry.register(CompressionPlugin())

# Use plugin
result = registry.transform("compression", content, {"model": "gpt-4"})
```

### PluginPipeline

Chain multiple plugins together for sequential transformations.

```python
from gptme.context import PluginPipeline, CompressionPlugin

# Create pipeline
pipeline = PluginPipeline([
    CompressionPlugin(),
    # Add more plugins
])

# Transform through pipeline
result = pipeline.transform(content, context)
```

## Built-in Plugins

### CompressionPlugin

Compresses context by stripping reasoning tags (`<think>`, `<thinking>`).

```python
from gptme.context import CompressionPlugin

plugin = CompressionPlugin()
result = plugin.transform(
    "Hello <think>reasoning</think> world",
    {"model": "gpt-4"}
)

print(result.content)  # "Hello world"
print(result.tokens_saved)  # Number of tokens saved
```

## Creating Custom Plugins

1. Subclass `ContextPlugin`
2. Implement required methods:
   - `name` property
   - `transform()` method
3. Optionally override:
   - `get_config_schema()` for configuration
   - `validate_config()` for validation

```python
from gptme.context import ContextPlugin, TransformResult

class FilterPlugin(ContextPlugin):
    @property
    def name(self) -> str:
        return "filter"

    def transform(self, content: str, context: dict) -> TransformResult:
        # Filter out specific patterns
        filtered = filter_sensitive_data(content)
        return TransformResult(
            content=filtered,
            metadata={"method": "filtering"}
        )
```

## Global Registry

Use the default registry for automatic plugin discovery:

```python
from gptme.context import get_registry, register_plugin, CompressionPlugin

# Register in default registry
register_plugin(CompressionPlugin())

# Access default registry
registry = get_registry()
result = registry.transform("compression", content, context)
```

## Future Plugins

Planned plugins for future development:
- RAGPlugin: Augment context with retrieved knowledge
- SummaryPlugin: Summarize long conversations
- FilterPlugin: Remove sensitive or irrelevant information
- CachePlugin: Cache and reuse transformed context
