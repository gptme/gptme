# Context Compression Preservation Feature

## Overview

The preservation feature ensures critical content elements (code blocks, headings) remain intact during context compression. This prevents information loss for structured content while still achieving compression goals.

## How It Works

The preservation algorithm uses a three-phase approach:

### Phase 1: Extraction and Placeholder Replacement

1. **Identify preservable elements:**
   - Code blocks: `` ```language ... ``` ``
   - Markdown headings: `# ...`, `## ...`, etc.

2. **Replace with unique placeholders:**
   ```
   Original: "Some text ```python\ncode()\n``` more text"
   Modified: "Some text [PRESERVE_0] more text"
   Mapping:  [PRESERVE_0] -> "```python\ncode()\n```"
   ```

3. **Sort by position (descending) to maintain correct string indices during replacement**

### Phase 2: Compression with Placeholders

1. **Split modified content into sentences** (placeholders included)
2. **Score sentences by relevance** to context
3. **Force-select sentences containing placeholders** to guarantee preservation
4. **Select additional sentences** to meet target compression ratio
5. **Reconstruct compressed text** with placeholders

### Phase 3: Re-insertion

1. **Replace all placeholders** with original preserved content
2. **Each placeholder appears exactly once** (no duplication)
3. **Return final compressed result**

## Configuration

```python
from gptme.context_compression.config import CompressionConfig

config = CompressionConfig(
    enabled=True,
    preserve_code=True,      # Preserve code blocks (default: True)
    preserve_headings=True,  # Preserve headings (default: True)
    target_ratio=0.7,        # Keep 70% of content
)
```

## Testing

Comprehensive test suite verifies:

- Code block preservation
- Heading preservation
- Mixed content handling
- No duplication
- No content expansion
- Preservation can be disabled

Run tests:
```bash
poetry run pytest tests/test_context_compression.py -v -k preserve
```

All 39 tests pass, including 6 new preservation-specific tests.
