---
type: skill
status: active
match:
  keywords: [python, repl, interactive]
  tools: [ipython]
scripts:
  - python_helpers.py
dependencies:
  - ipython
  - numpy
hooks:
  - pre_execute
  - post_execute
---

# Python REPL Skill

Interactive Python REPL automation with common helpers and best practices.

## Overview

This skill bundles Python REPL helpers, common imports, and execution patterns for efficient Python development in gptme.

## Bundled Scripts

### python_helpers.py

Provides utility functions for common Python tasks:
- Data inspection (inspect_df, describe_object)
- Quick plotting (quick_plot, show_image)
- Performance profiling (time_function)

## Usage Patterns

### Data Analysis
When working with data, automatically import common libraries and set up display options:

```python
import numpy as np
import pandas as pd
pd.set_option('display.max_rows', 100)
```

### Debugging
Use bundled helpers for debugging:

```python
from python_helpers import inspect_df, describe_object
inspect_df(df)  # Quick dataframe overview
describe_object(obj)  # Object introspection
```

## Hooks

### Pre-Execute Hook
- Validates Python environment
- Ensures required packages are installed
- Sets up common imports

### Post-Execute Hook
- Captures output and formats it
- Logs execution time for performance tracking

## Dependencies

- ipython: Interactive Python shell
- numpy: Numerical computing
- pandas: Data manipulation (optional, auto-imported if available)

## Best Practices

1. **Use helpers**: Leverage bundled helper functions instead of reimplementing
2. **Import once**: Common imports are handled by pre-execute hook
3. **Profile performance**: Use time_function for performance-sensitive code

## Examples

### Quick Data Analysis
```python
# Helpers auto-import pandas, numpy
df = pd.read_csv('data.csv')
inspect_df(df)  # Show overview
```

### Performance Profiling
```python
from python_helpers import time_function

@time_function
def slow_operation():
    # Your code here
    pass
```

## Related

- Lesson: [python](../../../docs/lessons/tools/python.md)
- Tool: ipython
