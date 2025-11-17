# Adaptive Compression API Guide

This guide shows how to use the adaptive compression system programmatically.

## Quick Start

```python
from gptme.context_compression import AdaptiveCompressor

# Create compressor
compressor = AdaptiveCompressor()

# Compress text with automatic task analysis
result = compressor.compress(
    text="Your content here...",
    task_description="Fix bug in user authentication",
    workspace_path=Path("/path/to/project")
)

# Access results
print(f"Original: {result.original_tokens} tokens")
print(f"Compressed: {result.compressed_tokens} tokens")
print(f"Reduction: {result.reduction_percentage:.1f}%")
print(f"Task complexity: {result.analysis.complexity_category}")
print(f"Compression ratio: {result.analysis.compression_ratio}")
```

## Components

### TaskAnalyzer

Analyzes tasks to determine complexity and select compression ratio.

```python
from gptme.context_compression.analyzer import TaskAnalyzer

analyzer = TaskAnalyzer()
analysis = analyzer.analyze(
    task_description="Implement user authentication system",
    workspace_path=Path("/path/to/project")
)

# Access analysis results
print(f"Complexity score: {analysis.complexity_score:.3f}")
print(f"Category: {analysis.complexity_category}")
print(f"Recommended ratio: {analysis.compression_ratio}")
print(f"Expected reduction: {analysis.estimated_reduction:.1f}%")
```

### AdaptiveCompressor

Combines task analysis with extractive compression.

```python
from gptme.context_compression import AdaptiveCompressor
from pathlib import Path

compressor = AdaptiveCompressor()

# Compress with task awareness
result = compressor.compress(
    text="Documentation and code to compress...",
    task_description="Add tests for API endpoints",
    workspace_path=Path.cwd()
)

# Manual ratio override (skip task analysis)
result = compressor.compress(
    text="Content...",
    compression_ratio=0.15  # Force 85% reduction
)
```

## Configuration

Configure via `gptme.toml`:

```toml
[compression]
enabled = true
mode = "adaptive"
log_analysis = true

[compression.complexity_thresholds]
focused = 0.3
architecture = 0.7

[compression.ratio_ranges]
focused = [0.10, 0.20]
mixed = [0.20, 0.30]
architecture = [0.30, 0.50]
```

See [Configuration Guide](configuration-adaptive-compression.md) for details.

## Analysis Details

The analyzer extracts task indicators across four dimensions:

**ScopeIndicators** (40% weight):
- File count mentions
- Line count estimates
- Implementation size hints

**DependencyIndicators** (30% weight):
- Import statements
- Class/function definitions
- External dependencies

**PatternIndicators** (20% weight):
- Action verbs (implement, refactor, debug)
- Architectural terms (system, service, integration)
- Complexity keywords

**ContextIndicators** (10% weight):
- Reference implementations available
- Tests in workspace
- Documentation quality

## Performance

The analyzer is highly efficient:
- Simple tasks: ~0.1ms
- Complex tasks: ~0.1ms
- With workspace: ~3ms
- **1000x faster than 100ms target**

## Integration Example

Integrate with existing code:

```python
from gptme.context_compression import AdaptiveCompressor
from gptme.config import get_config

def compress_context(context_text: str, task_prompt: str) -> str:
    """Compress context based on task complexity."""
    config = get_config()

    if not config.compression.enabled:
        return context_text

    compressor = AdaptiveCompressor()
    result = compressor.compress(
        text=context_text,
        task_description=task_prompt
    )

    if config.compression.log_analysis:
        print(f"Task: {result.analysis.complexity_category}")
        print(f"Ratio: {result.analysis.compression_ratio}")
        print(f"Saved: {result.reduction_percentage:.1f}%")

    return result.compressed_text
```

## Advanced Usage

### Custom Thresholds

```python
from gptme.context_compression.analyzer import TaskAnalyzer

analyzer = TaskAnalyzer(
    thresholds={"focused": 0.25, "architecture": 0.75},
    ratio_ranges={
        "focused": (0.15, 0.25),
        "mixed": (0.25, 0.35),
        "architecture": (0.35, 0.55)
    }
)
```

### Batch Processing

```python
compressor = AdaptiveCompressor()

tasks = [
    ("Fix bug in auth", "path/to/auth.py"),
    ("Implement API", "path/to/api.py"),
    ("Add tests", "path/to/tests/")
]

for description, file_path in tasks:
    text = Path(file_path).read_text()
    result = compressor.compress(text, task_description=description)
    print(f"{description}: {result.reduction_percentage:.1f}% reduction")
```

## See Also

- [Configuration Guide](configuration-adaptive-compression.md)
- [Migration Guide](migration-adaptive-compression.md)
- [Phase 3.1 Validation Results](../knowledge/technical/designs/context-compression-phase3-1-week5-validation.md)
