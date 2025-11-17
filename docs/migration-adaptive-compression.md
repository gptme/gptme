# Migration Guide: Adaptive Compression

This guide covers migrating from fixed compression to adaptive compression in gptme.

## Quick Migration

**Minimal change** - Enable adaptive mode:

```toml
# gptme.toml
[compression]
enabled = true
mode = "adaptive"  # Changed from "fixed"
```

That's it! Adaptive compression uses sensible defaults and works immediately.

## Migration Scenarios

### From No Compression

If compression is not currently enabled:

```toml
# Before
# [compression]
# (section missing or disabled)

# After
[compression]
enabled = true
mode = "adaptive"
```

### From Fixed Compression

If using fixed compression ratio:

```toml
# Before
[compression]
enabled = true
mode = "fixed"
ratio = 0.15

# After
[compression]
enabled = true
mode = "adaptive"
# No ratio needed - selected automatically
```

**Benefits of adaptive**:
- Focused tasks: More aggressive (0.10-0.20 vs fixed 0.15)
- Architecture tasks: More conservative (0.30-0.50 vs fixed 0.15)
- Mixed tasks: Moderate compression (0.20-0.30)

## Custom Configuration

Tune behavior for your use case:

```toml
[compression]
enabled = true
mode = "adaptive"
log_analysis = true  # Optional: see task analysis decisions

# Optional: Customize thresholds
[compression.complexity_thresholds]
focused = 0.3       # Default: 0.3
architecture = 0.7  # Default: 0.7

# Optional: Customize ratios
[compression.ratio_ranges]
focused = [0.10, 0.20]       # Default: [0.10, 0.20]
mixed = [0.20, 0.30]         # Default: [0.20, 0.30]
architecture = [0.30, 0.50]  # Default: [0.30, 0.50]
```

## Verification

Test adaptive compression is working:

```bash
# Run gptme with log_analysis enabled
gptme "Implement new feature"

# Should see analysis output:
# Task complexity: architecture
# Compression ratio: 0.433
# Estimated reduction: 56.7%
```

## Performance Impact

Adaptive compression adds minimal overhead:
- Analysis time: ~0.1-3ms (negligible)
- Benefits:
  - 30-80% token reduction (cost savings)
  - Better task-specific compression
  - Maintained quality (93.3% success rate)

## Rollback

If issues arise, temporarily disable:

```toml
[compression]
enabled = false
# or
mode = "fixed"
ratio = 0.20
```

## Expected Behavior Changes

### Before (Fixed 0.15 ratio)

- All tasks compressed equally
- Simple tasks: May have too much context
- Complex tasks: May miss critical info

### After (Adaptive)

- Task-aware compression
- **Focused tasks** (bug fixes, diagnostics):
  - More aggressive: 0.10-0.20 ratio
  - 80-90% reduction
  - Example: "Fix counter bug" → 0.133 ratio

- **Mixed tasks** (refactoring, updates):
  - Moderate: 0.20-0.30 ratio
  - 70-80% reduction
  - Example: "Refactor config" → 0.205 ratio

- **Architecture tasks** (implementations, designs):
  - Conservative: 0.30-0.50 ratio
  - 50-70% reduction
  - Example: "Implement auth system" → 0.433 ratio

## Validation Results

Phase 3.1 Week 5 validation with 15 test tasks:
- **Success rate**: 93.3% (14/15 passed)
- **Fixed regression**: Architecture tasks improved from 0% to 100%
- **Retained improvements**: Focused tasks maintained 100% success
- **Token reduction**: 30-80% depending on task complexity

See [validation results](../knowledge/technical/designs/context-compression-phase3-1-week5-validation.md) for details.

## Troubleshooting

**Issue**: Tasks misclassified

Check task description clarity. Add explicit scope indicators:
- "Fix bug in file.py" → focused
- "Implement auth across 5 files" → architecture
- "Refactor config loading" → mixed

**Issue**: Too aggressive compression

Increase ratio ranges:
```toml
[compression.ratio_ranges]
focused = [0.15, 0.25]  # Less aggressive
```

**Issue**: Not enough compression

Decrease ratio ranges:
```toml
[compression.ratio_ranges]
focused = [0.05, 0.15]  # More aggressive
```

## Best Practices

1. **Start with defaults**: Work well for most use cases
2. **Enable logging**: See analysis decisions during development
3. **Monitor success rate**: Track task completion quality
4. **Tune gradually**: Adjust thresholds based on actual results
5. **Document changes**: Note custom config for team

## Support

- Configuration issues: See [Configuration Guide](configuration-adaptive-compression.md)
- API usage: See [API Guide](api-adaptive-compression.md)
- Task analysis: Check logs with `log_analysis = true`

## Future: Phase 3.2

Upcoming enhancement (reference preservation):
- Detect reference implementations in workspace
- Preserve critical architectural context
- Further improve architecture task success

Migration to Phase 3.2 will be automatic when released.
