# CI Timeout Adjustment for Compression Tests

## Problem
Compression tests involve loading embedding models and running complex operations that can experience variable performance in CI environments.

## Solution
Increased CI workflow timeout from 8 to 12 minutes in `.github/workflows/test.yml`.

## Rationale
1. **Embedding Model Loading**: Compression tests load sentence-transformers models which can be slow on first load
2. **Test Complexity**: Multiple compression scenarios with sentence scoring and selection
3. **CI Variability**: GitHub Actions runners can experience performance variations
4. **Growth Buffer**: Provides headroom as the test suite expands

## Current Performance
- Current test suite: ~96 seconds (1.6 minutes)
- Previous timeout: 8 minutes
- New timeout: 12 minutes
- Buffer: 10+ minutes for growth and variability

## Implementation
Changed in `.github/workflows/test.yml`:
```yaml
timeout_minutes: 12  # previously 8
```

## Impact
- No breaking changes
- Tests continue to pass
- Better resilience to CI environment variations
- Room for test suite growth

## Commit
```
