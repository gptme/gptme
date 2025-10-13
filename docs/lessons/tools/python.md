---
match:
  keywords: [python, code, script, function, class]
  tools: [ipython]
---

# Python Development with IPython

Execute Python code interactively using the `ipython` tool.

## Best Practices

1. **Return values**: Last expression in a code block is automatically returned
2. **Use functions**: Break complex logic into reusable functions
3. **Import at top**: Keep imports organized at the start
4. **Test incrementally**: Run small pieces of code to verify behavior

## Common Patterns

### Data Analysis
```python
import pandas as pd
import numpy as np

# Read and explore data
df = pd.read_csv('data.csv')
df.head()
```

### File Operations
```python
from pathlib import Path

# Read file
content = Path('README.md').read_text()

# Write file
Path('output.txt').write_text(content)
```

### Working with APIs
```python
import requests

response = requests.get('https://api.example.com/data')
data = response.json()
```

## Available Libraries

The following libraries are pre-imported and available:
- `matplotlib` - Plotting and visualization
- `numpy` - Numerical computing
- `pandas` - Data analysis
- `PIL` - Image processing
- `scipy` - Scientific computing

## Debugging Tips

- Use `print()` for quick debugging
- Check types with `type(obj)`
- Inspect objects with `dir(obj)` or `help(obj)`
- Use `assert` statements to verify assumptions
