# Example Lessons

This directory contains example lessons that demonstrate the lesson system's capabilities.

## How Lessons Work

Lessons are automatically included in conversations when their keywords or tools match the context. They provide guidance and best practices at relevant moments.

## Lesson Format

```yaml
---
match:
  keywords: [keyword1, keyword2]
  tools: [tool1, tool2]
---

# Lesson Title

Lesson content in Markdown...
```

## Available Lessons

### Tools
- **patch.md** - Best practices for editing files with the patch tool
- **shell.md** - Shell command guidelines and common patterns
- **python.md** - Python development with IPython
- **browser.md** - Web browsing and automation

### Workflows
- **git.md** - Git workflow best practices and commit conventions

## Testing Lessons

To test if lessons are being included:

1. Use keywords or tools from a lesson in your conversation
2. Check the logs for "Indexed n lessons" message (appears once per conversation)
3. Use `/log` command to see included lessons (they're hidden by default)

## Configuration

Control lesson behavior with environment variables:

```bash
# Disable auto-include
export GPTME_LESSONS_AUTO_INCLUDE=false

# Limit number of lessons
export GPTME_LESSONS_MAX_INCLUDED=3
```

## Creating Your Own Lessons

1. Create `.md` files in `~/.config/gptme/lessons/` or `./lessons/` in your workspace
2. Add YAML frontmatter with keywords and/or tools
3. Write helpful content that will guide your work

Lessons are automatically indexed on first use in a conversation.
