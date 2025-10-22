# Example Lessons

This directory contains example lessons that demonstrate the lesson system's capabilities.

> **Note**: For complete documentation, see the main [Lessons Documentation](../lessons.rst).

## How Lessons Work

Lessons are automatically included in conversations when their keywords or tools match the context. They provide guidance and best practices at relevant moments.

The system adapts to different modes:
- **Interactive Mode**: Includes lessons based on user message keywords
- **Autonomous Mode**: Includes lessons based on assistant message keywords

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

## CLI Commands

The lesson system provides several commands for working with lessons:

- `/lesson list` - Show all available lessons
- `/lesson search <query>` - Search for lessons matching a query
- `/lesson show <id>` - Display a specific lesson
- `/lesson refresh` - Reload lessons from disk

## Testing Lessons

To test if lessons are being included:

1. Use keywords or tools from a lesson in your conversation
2. Check the logs for "Indexed n lessons" message (appears once per conversation)
3. Use `/log` command to see included lessons (they're hidden by default)
4. Use `/lesson search <keyword>` to verify a lesson would match

## Configuration

Control lesson behavior with environment variables:

```bash
# Disable auto-include (default: true)
export GPTME_LESSONS_AUTO_INCLUDE=false

# Limit number of lessons (default: 5)
export GPTME_LESSONS_MAX_INCLUDED=3

# Refresh lessons each message (default: false)
export GPTME_LESSONS_REFRESH=true

# Autonomous mode settings
export GPTME_LESSONS_AUTO_INCLUDE_AUTONOMOUS=true  # (default: false)
export GPTME_LESSONS_MAX_INCLUDED_AUTONOMOUS=3     # (default: 5)
```

**Autonomous vs Interactive Mode**:
- Interactive mode (>= 30% user messages): Uses standard settings
- Autonomous mode (< 30% user messages): Can use different limits
- Mode detected automatically based on message patterns

## Creating Your Own Lessons

1. Create `.md` files in `~/.config/gptme/lessons/` or `./lessons/` in your workspace
2. Add YAML frontmatter with keywords and/or tools
3. Write helpful content that will guide your work

Lessons are automatically indexed on first use in a conversation.

### Best Practices

**Keywords**:
- Use specific, relevant terms
- Include variations (e.g., "commit", "commits", "committing")
- 3-7 keywords per lesson is typical

**Content**:
- Keep lessons concise (< 100 lines preferred)
- Focus on one specific pattern or issue
- Include concrete examples
- Show both anti-patterns and solutions

**Structure**:
- Clear title
- Context section (when to use)
- Pattern section (what to do)
- Outcome section (expected results)

### Migrating Existing Lessons

If you have lessons without YAML frontmatter, they will still work but won't be auto-included. To enable auto-inclusion:

```markdown
---
match:
  keywords: [your, keywords, here]
  tools: [relevant, tools]
---

# Existing Lesson Title
... existing content ...
```

See the main [Lessons Documentation](../lessons.rst) for more details.
