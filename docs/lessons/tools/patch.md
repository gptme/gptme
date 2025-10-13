---
match:
  keywords: [patch, edit, modify, change]
  tools: [patch, morph]
---

# Editing Files with Patch Tool

When modifying existing files, use the `patch` tool to make incremental changes.

## Best Practices

1. **Keep patches focused**: Each patch should make one logical change
2. **Preserve context**: Include enough surrounding lines for clarity
3. **Avoid placeholders**: Don't use comments like `// ... rest of code ...`
4. **Test after patching**: Verify the changes work as expected

## Example

To add a new method to a class:

```patch example.py
<<<<<<< ORIGINAL
class MyClass:
    def existing_method(self):
        pass
=======
class MyClass:
    def existing_method(self):
        pass

    def new_method(self, arg):
        return arg * 2
>>>>>>> UPDATED
```

## When to Use Patch vs Save

- Use **patch** for: Small targeted changes, adding/modifying functions
- Use **save** for: New files, complete rewrites, when patch format conflicts with content
