# Adaptive Compression Configuration

This document describes the configuration options for adaptive compression in gptme.

## Overview

Adaptive compression automatically adjusts compression ratio based on task complexity. You can customize the behavior through configuration in `gptme.toml`.

## Configuration Options

### Basic Configuration

```toml
[compression]
enabled = true
mode = "adaptive"  # or "fixed"
log_analysis = true  # Log task analysis decisions
```

### Custom Complexity Thresholds

Control how tasks are classified by complexity:

```toml
[compression.complexity_thresholds]
focused = 0.3       # Tasks with score < 0.3 are "focused"
architecture = 0.7  # Tasks with score > 0.7 are "architecture"
```

**Default thresholds**:
- `focused = 0.3`: Simple tasks (bug fixes, diagnostics)
- `architecture = 0.7`: Complex tasks (implementations, designs)
- Between 0.3-0.7: Mixed tasks (refactoring, updates)

### Custom Compression Ratios

Customize compression aggressiveness for each category:

```toml
[compression.ratio_ranges]
focused = [0.10, 0.20]       # Aggressive compression for simple tasks
mixed = [0.20, 0.30]         # Moderate compression for mixed tasks
architecture = [0.30, 0.50]  # Conservative compression for complex tasks
```

**Default ranges**:
- `focused`: 0.10-0.20 (keep 10-20% of content, reduce 80-90%)
- `mixed`: 0.20-0.30 (keep 20-30% of content, reduce 70-80%)
- `architecture`: 0.30-0.50 (keep 30-50% of content, reduce 50-70%)

**Interpretation**:
- Lower ratio = more aggressive compression (keep less content)
- Higher ratio = more conservative compression (keep more content)

### Manual Override

Force a specific compression ratio for all tasks:

```toml
[compression]
manual_override_ratio = 0.25  # Force 25% ratio, skip analysis
```

When set, adaptive compression is disabled and all content is compressed at the specified ratio.

## Complete Example

```toml
[compression]
enabled = true
mode = "adaptive"
log_analysis = true

# Custom thresholds (more conservative)
[compression.complexity_thresholds]
focused = 0.2      # Lower threshold: more tasks classified as focused
architecture = 0.8  # Higher threshold: fewer tasks classified as architecture

# Custom ratios (less aggressive)
[compression.ratio_ranges]
focused = [0.15, 0.25]       # Keep 15-25% (vs default 10-20%)
mixed = [0.25, 0.35]         # Keep 25-35% (vs default 20-30%)
architecture = [0.35, 0.55]  # Keep 35-55% (vs default 30-50%)
```

## Use Cases

### Development Environment

More conservative compression for better debugging:

```toml
[compression]
mode = "adaptive"
[compression.ratio_ranges]
focused = [0.20, 0.30]
mixed = [0.30, 0.40]
architecture = [0.40, 0.60]
```

### Production Environment

Aggressive compression for cost savings:

```toml
[compression]
mode = "adaptive"
[compression.ratio_ranges]
focused = [0.08, 0.15]
mixed = [0.15, 0.25]
architecture = [0.25, 0.45]
```

### Testing/Debugging

Disable adaptive compression temporarily:

```toml
[compression]
mode = "fixed"
target_ratio = 0.7  # Fixed 30% reduction for all content
```

Or force specific ratio:

```toml
[compression]
mode = "adaptive"
manual_override_ratio = 0.3  # Test with 30% ratio
```

## Monitoring

Enable logging to see compression decisions:

```toml
[compression]
log_analysis = true
```

Example log output:
```text
Task analysis: complexity=0.25 (focused), ratio=0.18 (aggressive), reduction=~82%
Indicators: files=1, lines=50, deps=2, patterns=3, refs=0
Task: Fix counter increment bug in process function
```

## Performance

- Analysis overhead: <100ms per compression operation
- No impact on non-compressed content
- Configuration changes apply immediately (no restart needed)

## Related

- [Context Compression Phase 1](https://github.com/gptme/gptme/pull/834) - Initial implementation
- [Context Compression Phase 3.1](../knowledge/technical/designs/compression-phase3-1-task-analyzer-design.md) - Adaptive compression design
- [gptme Configuration](https://gptme.org/docs/config.html) - General configuration guide
