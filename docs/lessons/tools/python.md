---
match:
  keywords: [python, dependencies, packages, import error]
  tools: [ipython]
---

# Python in gptme: Dependencies and Environment

Practical guidance for working with Python in gptme.

## The Key Issue: ipython runs in gptme's environment

The `ipython` tool runs in gptme's Python environment, which means:
- ❌ **Don't** try to `pip install` packages inside ipython
- ❌ **Don't** use `pipx inject` to add packages to gptme
- ✅ **Do** use standalone scripts with uv for custom dependencies

## Solution: Use uv Script Dependencies

For scripts needing external packages, use **uv with inline dependencies** (PEP 723):

### Create a self-contained script:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "requests",
#   "rich",
# ]
# ///

import requests
from rich import print

response = requests.get('https://api.github.com')
print(response.json())
```

Make it executable and run directly:

```shell
chmod +x script.py
./script.py  # No need for "uv run" prefix!
```

This makes your script **truly self-contained and portable** - dependencies, Python version, and execution method all declared in the file itself.

uv automatically:
- Creates an isolated environment
- Installs dependencies
- Runs your script
- Caches everything for fast reruns

### Add dependencies to existing script:

```shell
uv add --script script.py 'requests<3' 'rich'
```

## When to Use ipython vs Scripts

### Use ipython for:
- Quick calculations and data exploration
- Testing code snippets
- Using built-in libraries (numpy, pandas, matplotlib, PIL, scipy)
- Using gptme-provided functions
- Interactive development

### Use shell + save for:
- Scripts with external dependencies (use uv script deps)
- Standalone programs
- Complex multi-file projects
- When you need specific package versions

## Common Issues

### Import Error for Package
**Problem**: `ModuleNotFoundError` in ipython
**Solution**: Create a script with uv deps instead

### Poetry/Dependency Conflicts
**Problem**: Version conflicts in project
**Solution**: Use uv for script isolation, avoid modifying gptme env

### Package Won't Install
**Problem**: Can't add package to ipython
**Solution**: Use uv script with inline dependencies

## Best Practices

1. **Default to ipython** for simple tasks with built-in libraries
2. **Use uv scripts** when you need external packages
3. **Keep scripts self-contained** with inline dependency declarations
4. **Don't modify gptme's environment** - use isolation instead
