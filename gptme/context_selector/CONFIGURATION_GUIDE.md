# Context Selector Configuration Guide

## Overview

This guide covers how to configure and tune the context selector for optimal performance. All configuration is done through dataclass-based config objects.

## Core Configuration

### ContextSelectorConfig

Main configuration for strategy selection and behavior.

```python
from gptme.context_selector.config import ContextSelectorConfig

config = ContextSelectorConfig(
    strategy="hybrid",  # "rule" | "llm" | "hybrid"
    model="gpt-4o-mini",  # LLM model for semantic selection
    batch_size=10,  # Process N candidates at once
    prefilter_ratio=0.5,  # For hybrid: keep top 50% from rule-based
    max_retries=3,  # LLM API retries
    timeout=30  # Seconds before timeout
)
```

### Strategy Selection

**When to use each strategy:**

| Strategy | Best For | Trade-offs |
|----------|----------|------------|
| `rule` | Speed-critical paths, well-defined metadata | Fast (<100ms), free, but less accurate |
| `llm` | Quality-critical, semantic understanding needed | Slow (1-3s), costly ($0.001-0.01), but most accurate |
| `hybrid` | Production default, balance needed | Balanced (500ms-1s, $0.0005-0.005) |

**Recommendation**: Start with `hybrid`, measure results, then optimize.

## Lesson Configuration

### LessonSelectorConfig

Controls how lessons are scored and selected.

```python
from gptme.lessons.selector_config import LessonSelectorConfig

config = LessonSelectorConfig(
    priority_boost={
        "critical": 3.0,  # Critical lessons get 3x score
        "high": 2.0,
        "normal": 1.0,
        "low": 0.5
    },
    category_weight={
        "workflow": 1.5,  # Workflow lessons preferred
        "tools": 1.3,
        "patterns": 1.2,
        "social": 1.0,
        "strategic": 1.0
    },
    use_metadata=True,  # Apply boosts
    max_lessons=5,  # Maximum to return
    min_score=0.0  # Minimum score threshold
)
```

### Priority Boost Tuning

**Purpose**: Prioritize important lessons over less critical ones.

**Guidelines**:
- `critical` (3.0): Safety, data loss prevention, blocking issues
- `high` (2.0): Common errors, workflow best practices
- `normal` (1.0): General guidance, optional patterns
- `low` (0.5): Nice-to-have, rarely needed

**Tuning**:
- Increase gap (e.g., 4.0/2.0/1.0/0.25) if critical lessons not appearing enough
- Decrease gap (e.g., 1.5/1.2/1.0/0.8) if too much bias toward critical

### Category Weight Tuning

**Purpose**: Prefer certain lesson categories based on agent context.

**Guidelines**:
- `workflow` (1.5): High for autonomous agents, processes
- `tools` (1.3): High for implementation-heavy work
- `patterns` (1.2): High for architecture/design work
- `social` (1.0): Baseline for community interaction
- `strategic` (1.0): Baseline for planning work

**Tuning**:
- Increase weights for categories your agent uses most
- Set to 1.0 for categories you want neutral treatment
- Set < 1.0 (e.g., 0.7) to de-prioritize rarely-used categories

### Composite Scoring Formula

```text
final_score = base_score × priority_boost × category_weight

Example:
- base_score: 0.8 (from LLM or rule-based)
- priority: "high" → 2.0x
- category: "workflow" → 1.5x
- final_score: 0.8 × 2.0 × 1.5 = 2.4
```

## File Configuration

### FileSelectorConfig

Controls how files are scored and selected.

```python
from gptme.context_selector.file_config import FileSelectorConfig

config = FileSelectorConfig(
    mention_boost={
        10: 3.0,  # 10+ mentions
        5: 2.0,   # 5-9 mentions
        2: 1.5,   # 2-4 mentions
        1: 1.0    # 1 mention
    },
    recency_boost={
        3600: 1.3,    # Modified <1hr ago
        86400: 1.1,   # Modified <24hr ago
        604800: 1.05  # Modified <1wk ago
    },
    file_type_weight={
        ".py": 1.3,   # Python files
        ".md": 1.2,   # Documentation
        ".toml": 1.1, # Config files
        ".yaml": 1.1
    },
    strategy="hybrid",  # Selection strategy
    max_files=10  # Maximum to return
)
```

### Mention Boost Tuning

**Purpose**: Prioritize files that are frequently referenced in conversation.

**Guidelines**:
- High thresholds (10+, 5+) for codebases with many files
- Lower thresholds (3+, 2+) for smaller projects
- Bigger multipliers (4.0, 2.5) if mentions are rare but critical
- Smaller multipliers (2.0, 1.5) if most files get mentioned

**Tuning**:
```python
# For large codebases (1000+ files)
mention_boost={20: 4.0, 10: 2.5, 5: 1.5, 1: 1.0}

# For small projects (100 files)
mention_boost={5: 2.0, 3: 1.5, 1: 1.0}
```

### Recency Boost Tuning

**Purpose**: Prefer recently modified files (likely still relevant).

**Guidelines**:
- Conservative boosts (1.3, 1.1, 1.05) since mentions more important than recency
- Use 1hr/24hr/1wk thresholds for typical development cadence
- Adjust for your workflow (e.g., hourly vs daily updates)

**Tuning**:
```python
# For fast-moving projects (hourly updates)
recency_boost={1800: 1.4, 3600: 1.2, 86400: 1.05}  # 30min, 1hr, 24hr

# For slower projects (weekly updates)
recency_boost={86400: 1.2, 604800: 1.05, 2592000: 1.0}  # 1d, 1w, 1mo
```

### File Type Weight Tuning

**Purpose**: Prefer certain file types based on task.

**Guidelines**:
- Code files (`.py`, `.ts`) higher for implementation work
- Docs (`.md`) higher for learning/research work
- Config (`.toml`, `.yaml`) higher for setup/deployment work

**Tuning**:
```python
# For code-heavy work
file_type_weight={".py": 1.5, ".ts": 1.4, ".md": 1.0}

# For documentation work
file_type_weight={".md": 1.5, ".rst": 1.3, ".py": 1.0}

# For DevOps work
file_type_weight={".yaml": 1.4, ".toml": 1.3, ".sh": 1.2, ".py": 1.0}
```

## Model Selection

### Recommended Models

| Model | Cost | Latency | Quality | Use Case |
|-------|------|---------|---------|----------|
| `gpt-4o-mini` | Lowest | Fast | Good | Production default |
| `gpt-4o` | Medium | Medium | Better | When quality matters |
| `anthropic/claude-haiku-4-5` | Low | Fast | Good | Cost-sensitive |
| `anthropic/claude-sonnet-4-5` | High | Slow | Best | Critical decisions |

### Tuning Guidelines

1. **Start with `gpt-4o-mini`**: Cheapest, fast, good enough for most cases
2. **Measure cost**: Track daily LLM usage (see Monitoring section)
3. **Optimize if needed**:
   - Cost too high? → Use rule-based for some paths
   - Quality issues? → Try `gpt-4o` or `claude-sonnet-4-5`
   - Latency issues? → Increase `batch_size`, use `prefilter_ratio`

## Batch Size Tuning

```python
config = ContextSelectorConfig(
    batch_size=10  # Process 10 candidates at once
)
```

**Trade-offs**:
- Small batch (5-10): Lower latency, more LLM calls
- Large batch (20-50): Higher latency per call, fewer calls overall
- Very large (100+): May exceed model context limits

**Recommendation**:
- Start with 10
- Increase to 20-30 if you have 100+ candidates
- Decrease to 5 if latency is critical

## Prefilter Ratio (Hybrid Only)

```python
config = ContextSelectorConfig(
    strategy="hybrid",
    prefilter_ratio=0.5  # Keep top 50% from rule-based
)
```

**Purpose**: Balance between speed (rule-based) and quality (LLM).

**Tuning**:
- `0.3` (30%): More LLM filtering, better quality, higher cost
- `0.5` (50%): Balanced (default)
- `0.7` (70%): More rule-based, faster, lower cost

**Recommendation**: Start with 0.5, adjust based on quality/cost metrics.

## Cost Optimization

### Strategies to Reduce Cost

1. **Use rule-based for known patterns**:
   ```python
   # Fast paths use rule-based
   if is_simple_query(query):
       config = ContextSelectorConfig(strategy="rule")
   else:
       config = ContextSelectorConfig(strategy="hybrid")
   ```

2. **Increase prefilter ratio**:
   ```python
   config = ContextSelectorConfig(
       strategy="hybrid",
       prefilter_ratio=0.7  # 70% filtered by rules
   )
   ```

3. **Use cheaper models**:
   ```python
   config = ContextSelectorConfig(
       model="gpt-4o-mini"  # vs gpt-4o or claude-sonnet
   )
   ```

4. **Increase batch size**:
   ```python
   config = ContextSelectorConfig(
       batch_size=30  # Fewer API calls
   )
   ```

## Monitoring and Metrics

### What to Track

1. **Selection metrics**:
   - Selections per day
   - Average candidates per selection
   - Average results per selection

2. **Cost metrics**:
   - Daily LLM API spend
   - Cost per selection
   - Tokens used per selection

3. **Quality metrics**:
   - User satisfaction (explicit feedback)
   - Task success rate
   - Time to resolution

4. **Performance metrics**:
   - Average latency (p50, p95, p99)
   - Cache hit rate (if caching implemented)
   - Fallback frequency (LLM failures)

### Example Monitoring

```python
import logging
from time import time

logger = logging.getLogger(__name__)

async def select_with_metrics(selector, query, candidates):
    start = time()
    try:
        results = await selector.select(query, candidates)
        latency = time() - start

        logger.info(
            "Selection completed",
            extra={
                "latency_ms": latency * 1000,
                "num_candidates": len(candidates),
                "num_results": len(results),
                "strategy": selector.config.strategy
            }
        )
        return results
    except Exception as e:
        logger.error(f"Selection failed: {e}")
        # Use fallback...
```

## Environment-Specific Configs

### Development

```python
# Fast iteration, low cost
dev_config = ContextSelectorConfig(
    strategy="rule",  # Fast, free
    model="gpt-4o-mini"  # Cheap for testing
)
```

### Staging

```python
# Test production config
staging_config = ContextSelectorConfig(
    strategy="hybrid",
    prefilter_ratio=0.5,
    model="gpt-4o-mini",
    batch_size=20
)
```

### Production

```python
# Balanced quality and cost
prod_config = ContextSelectorConfig(
    strategy="hybrid",
    prefilter_ratio=0.5,
    model="gpt-4o-mini",
    batch_size=20,
    max_retries=3,
    timeout=30
)
```

## Next Steps

- **API Guide**: See [API_GUIDE.md](./API_GUIDE.md) for usage examples
- **Migration Guide**: See [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md) for integration
- **Benchmarking**: Run benchmarks to validate configuration choices
