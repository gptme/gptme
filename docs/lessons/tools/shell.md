---
match:
  keywords: [shell, command, bash, terminal, execute]
  tools: [shell]
---

# Working with Shell Commands

Execute shell commands to interact with the system, files, and tools.

## Best Practices

1. **Check before destructive operations**: Use `ls`, `git status`, etc. before `rm` or `git reset`
2. **Use absolute paths**: Relative paths can break if working directory changes
3. **Proper quoting**: Use single quotes for content with special characters
4. **Chain related commands**: Use `&&` to only continue if previous command succeeds

## Common Patterns

### Exploring the filesystem
```shell
# List files
ls -la

# Find files
find . -name "*.py" -type f

# Check directory structure
tree -L 2
```

### Git operations
```shell
# Check status before committing
git status

# Stage specific files
git add path/to/file.py

# Never use git add . or git add -A
```

### File operations
```shell
# Read files
cat README.md

# Search in files
grep -r "pattern" src/

# Copy with structure
cp -r src/ backup/
```

## Avoiding Common Mistakes

- ❌ Don't use `rm -rf` without careful review
- ❌ Avoid `git commit -a` - stage files explicitly
- ✅ Use `pwd` to verify current directory
- ✅ Test commands on small data first
