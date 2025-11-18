# Context Selector Migration Guide

## Overview

This guide shows how to integrate the context selector into existing gptme code. The system is designed for **backward compatibility** - existing code continues working with `use_selector=False` as default.

## Migration Strategy

**Recommended approach**: Gradual rollout with feature flags

1. **Phase 1**: Deploy with `use_selector=False` (existing behavior)
2. **Phase 2**: Test with `use_selector=True` in staging
3. **Phase 3**: A/B test in production (50/50 split)
4. **Phase 4**: Roll out to 100% based on metrics

## Lesson System Integration

### Before (Existing Code)

```python
from gptme.lessons import get_lessons, match_lessons

# Load lessons
lessons = get_lessons()

# Match using keyword-based approach
matched = match_lessons(lessons, message="I need to use git worktrees")

# Result: Lessons matching keywords in message
```

### After (With Context Selector)

```python
from gptme.lessons.matcher_enhanced import EnhancedLessonMatcher
from gptme.context_selector.config import ContextSelectorConfig
from gptme.lessons.selector_config import LessonSelectorConfig

# Create enhanced matcher
matcher = EnhancedLessonMatcher(
    selector_config=ContextSelectorConfig(strategy="hybrid"),
    lesson_config=LessonSelectorConfig(),
    use_selector=True  # Enable LLM selection
)

# Match using LLM + metadata
matched = await matcher.match_with_selector(
    lessons=all_lessons,
    context=match_context,
    max_results=5
)

# Result: Semantically relevant lessons with metadata boosts
```

### Feature Flag Pattern

```python
import os

USE_SELECTOR = os.getenv("GPTME_USE_CONTEXT_SELECTOR", "false").lower() == "true"

if USE_SELECTOR:
    matcher = EnhancedLessonMatcher(
        selector_config=ContextSelectorConfig(strategy="hybrid"),
        lesson_config=LessonSelectorConfig(),
        use_selector=True
    )
    matched = await matcher.match_with_selector(lessons, context)
else:
    # Existing behavior
    matched = match_lessons(lessons, message)
```

### Converting Message to MatchContext

The enhanced matcher needs a `MatchContext` object:

```python
from gptme.lessons.matcher import MatchContext

# Convert messages to MatchContext
def messages_to_context(messages: list[Message]) -> MatchContext:
    """Convert conversation messages to match context."""
    # Combine recent messages into context string
    context_parts = []
    for msg in messages[-10:]:  # Last 10 messages
        if msg.role != "system":
            context_parts.append(f"{msg.role}: {msg.content[:500]}")

    context_str = "\n".join(context_parts)

    return MatchContext(
        text=context_str,
        keywords=extract_keywords(context_str),  # Optional
        tools_used=extract_tools(messages)  # Optional
    )

# Use in matcher
context = messages_to_context(conversation)
matched = await matcher.match_with_selector(lessons, context)
```

## File Context Integration

### Before (Existing Code)

```python
from gptme.context import get_mentioned_files

# Get files by mention count + mtime
files = get_mentioned_files(
    messages=conversation,
    workspace=Path("/path/to/workspace")
)

# Get top 10
top_files = files[:10]
```

### After (With Context Selector)

```python
from gptme.context_selector.file_selector import select_relevant_files
from gptme.context_selector.file_config import FileSelectorConfig

# Select using LLM + metadata
files = await select_relevant_files(
    messages=conversation,
    workspace=Path("/path/to/workspace"),
    max_files=10,
    use_selector=True,
    config=FileSelectorConfig(strategy="hybrid")
)

# Result: Semantically relevant files with metadata boosts
```

### Feature Flag Pattern

```python
import os

USE_SELECTOR = os.getenv("GPTME_USE_FILE_SELECTOR", "false").lower() == "true"

if USE_SELECTOR:
    files = await select_relevant_files(
        messages, workspace,
        use_selector=True,
        config=FileSelectorConfig(strategy="hybrid")
    )
else:
    # Existing behavior
    files = get_mentioned_files(messages, workspace)[:10]
```

### Drop-in Replacement

For minimal code changes, `select_relevant_files` can be a drop-in replacement:

```python
# Old code
from gptme.context import get_mentioned_files
files = get_mentioned_files(msgs, workspace)[:10]

# New code (drop-in with use_selector=False)
from gptme.context_selector.file_selector import select_relevant_files
files = await select_relevant_files(msgs, workspace, use_selector=False)

# Enable selector later via config
files = await select_relevant_files(msgs, workspace, use_selector=True)
```

## Custom Content Type Integration

### Creating a Custom ContextItem

```python
from gptme.context_selector.base import ContextItem
from typing import Any

class MyContentItem(ContextItem):
    """Wrapper for custom content type."""

    def __init__(self, content_obj):
        self.obj = content_obj

    @property
    def content(self) -> str:
        """Text content for LLM evaluation."""
        return self.obj.get_text()

    @property
    def metadata(self) -> dict[str, Any]:
        """Metadata for boosting/filtering."""
        return {
            "priority": self.obj.priority,
            "category": self.obj.category,
            "tags": self.obj.tags,
            "created": self.obj.created.isoformat(),
            # Add custom metadata...
        }

    @property
    def identifier(self) -> str:
        """Unique identifier."""
        return self.obj.id
```

### Creating Custom Config

```python
from dataclasses import dataclass, field
from gptme.context_selector.config import ContextSelectorConfig

@dataclass
class MyContentSelectorConfig(ContextSelectorConfig):
    """Configuration for custom content selection."""

    # Custom boost mappings
    priority_boost: dict[str, float] = field(
        default_factory=lambda: {
            "urgent": 3.0,
            "high": 2.0,
            "normal": 1.0,
            "low": 0.5
        }
    )

    category_weight: dict[str, float] = field(
        default_factory=lambda: {
            "important": 1.5,
            "standard": 1.0,
            "optional": 0.7
        }
    )

    def apply_boosts(self, base_score: float, metadata: dict) -> float:
        """Apply custom metadata boosts."""
        priority = metadata.get("priority", "normal")
        category = metadata.get("category", "standard")

        boost = self.priority_boost.get(priority, 1.0)
        weight = self.category_weight.get(category, 1.0)

        return base_score * boost * weight
```

### Using Custom Selector

```python
from gptme.context_selector.hybrid import HybridSelector

# Create config
config = MyContentSelectorConfig(
    strategy="hybrid",
    prefilter_ratio=0.5
)

# Create selector
selector = HybridSelector(config)

# Wrap your content
items = [MyContentItem(obj) for obj in my_content_objects]

# Select
selected = await selector.select(
    query="user question or context",
    candidates=items,
    max_results=5
)

# Unwrap results
results = [item.obj for item in selected]
```

## Testing Migrations

### Unit Testing

```python
import pytest
from gptme.context_selector.rule_based import RuleBasedSelector
from gptme.context_selector.config import ContextSelectorConfig

@pytest.mark.asyncio
async def test_rule_based_selection():
    """Test rule-based selection works."""
    config = ContextSelectorConfig(strategy="rule")
    selector = RuleBasedSelector(config)

    items = create_test_items()
    selected = await selector.select(
        query="test query",
        candidates=items,
        max_results=3
    )

    assert len(selected) <= 3
    assert all(isinstance(item, ContextItem) for item in selected)
```

### Integration Testing

```python
@pytest.mark.asyncio
async def test_lesson_selector_integration():
    """Test lesson selector with real lessons."""
    matcher = EnhancedLessonMatcher(
        selector_config=ContextSelectorConfig(strategy="hybrid"),
        use_selector=True
    )

    lessons = load_test_lessons()
    context = create_test_context("I need to use git worktrees")

    results = await matcher.match_with_selector(
        lessons=lessons,
        context=context,
        max_results=5
    )

    assert len(results) > 0
    assert results[0].score > 0
```

### A/B Testing

```python
import random

async def select_lessons_with_ab_test(lessons, context):
    """A/B test: 50% use selector, 50% use keyword matching."""
    use_selector = random.random() < 0.5

    if use_selector:
        matcher = EnhancedLessonMatcher(use_selector=True)
        results = await matcher.match_with_selector(lessons, context)
        method = "selector"
    else:
        results = match_lessons(lessons, context.text)
        method = "keyword"

    # Log for analysis
    logger.info(f"Lesson selection method: {method}, results: {len(results)}")

    return results, method
```

## Performance Considerations

### Caching

Consider caching for frequently repeated queries:

```python
from functools import lru_cache
from typing import Tuple

@lru_cache(maxsize=100)
def cached_select(query: str, candidates_hash: int) -> Tuple[str, ...]:
    """Cache selection results by query + candidates hash."""
    # Note: Can't cache ContextItem objects directly (not hashable)
    # Cache identifiers instead
    pass

async def select_with_cache(selector, query, candidates):
    """Select with cache layer."""
    # Create hash of candidates
    candidates_hash = hash(tuple(item.identifier for item in candidates))

    cached = cached_select(query, candidates_hash)
    if cached:
        return [item for item in candidates if item.identifier in cached]

    # No cache hit, do selection
    results = await selector.select(query, candidates)

    # Cache identifiers
    cached_select(query, candidates_hash)
    cached_select.cache_info()  # For monitoring

    return results
```

### Async Batching

For high-throughput scenarios:

```python
from asyncio import gather

async def select_batch(selector, queries_and_candidates):
    """Process multiple selections in parallel."""
    tasks = [
        selector.select(query, candidates)
        for query, candidates in queries_and_candidates
    ]
    return await gather(*tasks)
```

## Monitoring in Production

### Metrics to Track

```python
from prometheus_client import Counter, Histogram

# Counters
selections_total = Counter(
    'context_selector_selections_total',
    'Total selections performed',
    ['strategy', 'content_type']
)

selection_errors = Counter(
    'context_selector_errors_total',
    'Selection errors',
    ['strategy', 'error_type']
)

# Histograms
selection_latency = Histogram(
    'context_selector_latency_seconds',
    'Selection latency',
    ['strategy']
)

selection_results = Histogram(
    'context_selector_results',
    'Number of results returned',
    ['strategy']
)

# Usage
async def select_with_metrics(selector, query, candidates):
    strategy = selector.config.strategy

    with selection_latency.labels(strategy=strategy).time():
        try:
            results = await selector.select(query, candidates)
            selections_total.labels(
                strategy=strategy,
                content_type="lesson"
            ).inc()
            selection_results.labels(strategy=strategy).observe(len(results))
            return results
        except Exception as e:
            selection_errors.labels(
                strategy=strategy,
                error_type=type(e).__name__
            ).inc()
            raise
```

## Rollback Strategy

If issues arise, rollback is simple:

```python
# Emergency rollback: Set environment variable
export GPTME_USE_CONTEXT_SELECTOR=false

# Or change default in code
USE_SELECTOR = False  # Was: True

# Existing behavior immediately restored
```

## Common Pitfalls

### 1. Forgetting `await`

```python
# Wrong: Missing await
results = matcher.match_with_selector(lessons, context)  # Returns coroutine

# Correct
results = await matcher.match_with_selector(lessons, context)
```

### 2. Using synchronous code in async context

```python
# Wrong: Mixing sync/async
def process_lessons(lessons, context):
    matcher = EnhancedLessonMatcher(use_selector=True)
    results = await matcher.match_with_selector(lessons, context)  # SyntaxError

# Correct: Make function async
async def process_lessons(lessons, context):
    matcher = EnhancedLessonMatcher(use_selector=True)
    results = await matcher.match_with_selector(lessons, context)
```

### 3. Not handling LLM failures

```python
# Wrong: No error handling
results = await selector.select(query, candidates)

# Correct: Graceful fallback
try:
    results = await selector.select(query, candidates)
except Exception as e:
    logger.warning(f"Selector failed: {e}, using fallback")
    results = rule_based_fallback(query, candidates)
```

## Migration Checklist

- [ ] Add feature flag environment variable
- [ ] Update imports to use enhanced matcher/selector
- [ ] Convert messages to MatchContext format
- [ ] Add `await` to async selector calls
- [ ] Add error handling with fallback
- [ ] Test with `use_selector=False` (existing behavior)
- [ ] Test with `use_selector=True` (new behavior)
- [ ] Add monitoring metrics
- [ ] Document rollback procedure
- [ ] Plan gradual rollout (staging → A/B → full)

## Next Steps

- **API Guide**: See [API_GUIDE.md](./API_GUIDE.md) for detailed usage
- **Configuration**: See [CONFIGURATION_GUIDE.md](./CONFIGURATION_GUIDE.md) for tuning
- **Examples**: See [examples/](./examples/) for complete integration examples
