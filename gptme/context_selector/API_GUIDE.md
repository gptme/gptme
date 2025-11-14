# Context Selector API Guide

## Overview

The context selector provides LLM-powered selection of relevant context items (lessons, files, etc.) based on conversation content. It supports three strategies:

- **Rule-based**: Fast keyword/metadata matching (existing behavior)
- **LLM-based**: Semantic relevance via LLM evaluation
- **Hybrid**: Combines both approaches for balanced results

## Core Interfaces

### ContextItem (ABC)

Base interface for items that can be selected as context.

```python
from gptme.context_selector.base import ContextItem

class MyItem(ContextItem):
    @property
    def content(self) -> str:
        """Text content for LLM evaluation."""
        return self.data.body

    @property
    def metadata(self) -> dict[str, Any]:
        """Metadata for boosting/filtering."""
        return {
            "priority": "high",
            "category": "workflow",
            "tags": ["git", "automation"]
        }

    @property
    def identifier(self) -> str:
        """Unique identifier."""
        return self.data.path
```

### ContextSelector (ABC)

Base interface for selection strategies.

```python
from gptme.context_selector.base import ContextSelector

async def select(
    query: str,
    candidates: list[ContextItem],
    max_results: int = 5
) -> list[ContextItem]:
    """Select most relevant items from candidates."""
    pass
```

## Strategy Implementations

### 1. Rule-Based Selection

Fast, deterministic selection using metadata matching.

```python
from gptme.context_selector.rule_based import RuleBasedSelector
from gptme.context_selector.config import ContextSelectorConfig

config = ContextSelectorConfig(strategy="rule")
selector = RuleBasedSelector(config)

selected = await selector.select(
    query="how to use git worktrees",
    candidates=lesson_items,
    max_results=5
)
```

**Use when**:
- Need fast selection (<100ms)
- Have well-defined metadata (keywords, tags)
- Deterministic results preferred
- Cost is a concern

### 2. LLM-Based Selection

Semantic relevance via LLM evaluation.

```python
from gptme.context_selector.llm_based import LLMBasedSelector
from gptme.context_selector.config import ContextSelectorConfig

config = ContextSelectorConfig(
    strategy="llm",
    model="gpt-4o-mini",  # Fast and cheap
    batch_size=10  # Process 10 at a time
)
selector = LLMBasedSelector(config)

selected = await selector.select(
    query="I need to create a pull request",
    candidates=lesson_items,
    max_results=5
)
```

**Use when**:
- Need semantic understanding
- Keywords not sufficient
- Can tolerate cost/latency
- Quality over speed

**Cost**: ~$0.001-0.01 per selection (depends on candidates, model)

### 3. Hybrid Selection

Combines rule-based pre-filtering with LLM refinement.

```python
from gptme.context_selector.hybrid import HybridSelector
from gptme.context_selector.config import ContextSelectorConfig

config = ContextSelectorConfig(
    strategy="hybrid",
    prefilter_ratio=0.5,  # Rule-based keeps top 50%
    model="gpt-4o-mini"
)
selector = HybridSelector(config)

selected = await selector.select(
    query="debugging frontend issues",
    candidates=lesson_items,
    max_results=5
)
```

**Use when**:
- Need balance of speed and quality
- Large candidate sets (100+)
- Want cost optimization
- Default recommended strategy

## Lesson Integration (Phase 2)

### Basic Usage

```python
from gptme.lessons.matcher_enhanced import EnhancedLessonMatcher
from gptme.context_selector.config import ContextSelectorConfig
from gptme.lessons.selector_config import LessonSelectorConfig

# Create matcher with selector
matcher = EnhancedLessonMatcher(
    selector_config=ContextSelectorConfig(strategy="hybrid"),
    lesson_config=LessonSelectorConfig(),
    use_selector=True
)

# Match lessons to conversation
results = await matcher.match_with_selector(
    lessons=all_lessons,
    context=match_context,
    max_results=5
)
```

### Metadata Boosts

Lessons can have priority and category metadata that boosts their scores:

```python
from gptme.lessons.selector_config import LessonSelectorConfig

config = LessonSelectorConfig(
    priority_boost={
        "critical": 3.0,  # 3x multiplier
        "high": 2.0,
        "normal": 1.0,
        "low": 0.5
    },
    category_weight={
        "workflow": 1.5,  # 1.5x multiplier
        "tools": 1.3,
        "patterns": 1.2,
        "social": 1.0
    }
)

# Final score = base_score × priority_boost × category_weight
```

### Backward Compatibility

By default, `use_selector=False` maintains existing keyword matching:

```python
# Old behavior (keyword matching)
matcher = EnhancedLessonMatcher(use_selector=False)
results = matcher.match(lessons, context)

# New behavior (LLM selection)
matcher = EnhancedLessonMatcher(use_selector=True)
results = await matcher.match_with_selector(lessons, context)
```

## File Integration (Phase 3)

### Basic Usage

```python
from gptme.context_selector.file_selector import select_relevant_files
from gptme.context_selector.file_config import FileSelectorConfig

# Select files using context selector
files = await select_relevant_files(
    messages=conversation,
    workspace=Path("/path/to/workspace"),
    max_files=10,
    use_selector=True,
    config=FileSelectorConfig(strategy="hybrid")
)
```

### Metadata Boosts

Files are scored based on mention count, recency, and file type:

```python
from gptme.context_selector.file_config import FileSelectorConfig

config = FileSelectorConfig(
    mention_boost={
        10: 3.0,  # 10+ mentions → 3x boost
        5: 2.0,   # 5-9 mentions → 2x boost
        2: 1.5,   # 2-4 mentions → 1.5x boost
        1: 1.0    # 1 mention → 1x boost
    },
    recency_boost={
        3600: 1.3,    # <1hr → 1.3x boost
        86400: 1.1,   # <24hr → 1.1x boost
        604800: 1.05  # <1week → 1.05x boost
    },
    file_type_weight={
        ".py": 1.3,
        ".md": 1.2,
        ".toml": 1.1,
        ".yaml": 1.1
    }
)

# Final score = base_score × mention_boost × recency_boost × type_weight
```

### Backward Compatibility

Falls back to `get_mentioned_files()` when `use_selector=False`:

```python
# Old behavior (simple mention count + mtime sort)
files = await select_relevant_files(
    messages, workspace,
    use_selector=False
)

# New behavior (LLM-based selection with metadata boosts)
files = await select_relevant_files(
    messages, workspace,
    use_selector=True,
    config=FileSelectorConfig(strategy="hybrid")
)
```

## Performance Characteristics

| Strategy | Latency | Cost | Accuracy | Use Case |
|----------|---------|------|----------|----------|
| Rule-based | <100ms | Free | Good | Fast, deterministic |
| LLM-based | 1-3s | $0.001-0.01 | Best | Semantic understanding |
| Hybrid | 500ms-1s | $0.0005-0.005 | Better | Balanced (recommended) |

## Error Handling

All selectors handle errors gracefully:

```python
try:
    selected = await selector.select(query, candidates)
except Exception as e:
    # Falls back to rule-based selection
    logger.warning(f"Selection failed: {e}, using fallback")
    selected = rule_based_fallback(candidates)
```

## Next Steps

- **Configuration Guide**: See [CONFIGURATION_GUIDE.md](./CONFIGURATION_GUIDE.md) for parameter tuning
- **Migration Guide**: See [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md) for integration instructions
- **Examples**: See [examples/](./examples/) for complete usage examples
