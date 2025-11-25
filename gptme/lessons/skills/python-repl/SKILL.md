---
name: python-repl
description: Interactive Python REPL automation with common helpers and best practices
---

# Python REPL Skill

Enhances Python REPL workflows with bundled utility functions for data analysis, debugging, and performance profiling.

## Overview

This skill bundles Python REPL helpers, common imports, and execution patterns for efficient Python development in gptme.

## Bundled Scripts

### Helper Functions (python_helpers.py)

This skill includes bundled utility functions for common Python tasks:
- Data inspection (inspect_df, describe_object)
- Quick plotting (quick_plot)
- Performance profiling (time_function)
- Common imports setup (setup_common_imports)

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

## Dependencies

Required packages are listed in `requirements.txt`:
- ipython: Interactive Python shell
- numpy: Numerical computing
- pandas: Data manipulation

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

- Tool: ipython
