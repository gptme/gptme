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


```

## Creating Your Own Lessons

1. Create `.md` files in `~/.config/gptme/lessons/` or `./lessons/` in your workspace
2. Add YAML frontmatter with keywords and/or tools
3. Write helpful content that will guide your work

Lessons are automatically indexed on first use in a conversation.

**See also**:
- [EXAMPLES.md](EXAMPLES.md) - Real-world lesson examples and patterns
- [CONTRIBUTING.md](CONTRIBUTING.md) - Guidelines for contributing lessons
- [TESTING.md](TESTING.md) - Testing and validation across platforms

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

## Cursor Rules Integration

gptme supports integration with Cursor editor's `.cursorrules` files, enabling you to use project-specific coding standards and guidelines from Cursor in your gptme workflows.

### How It Works

1. **Automatic Detection**: When gptme starts in a directory with a `.cursorrules` file, it detects and provides conversion guidance
2. **Manual Conversion**: Convert Cursor rules to gptme lesson format using the conversion tool
3. **Project-Local Lessons**: Place converted lessons in `.gptme/lessons/` for project-specific guidance

### Converting Cursor Rules

#### Prerequisites

Clone or have access to `gptme-contrib`:

```bash
git clone https://github.com/gptme/gptme-contrib
```

#### Conversion Workflow

```bash
# From your project root with .cursorrules file
cd path/to/gptme-contrib/cursorrules

# Convert to lesson format
python3 cursorrules_parser.py to-lesson /path/to/project/.cursorrules /path/to/project/.gptme/lessons/project-rules.md

# Verify the conversion
python3 cursorrules_parser.py parse /path/to/project/.cursorrules
```

#### Conversion Tool Commands

The `cursorrules_parser.py` tool provides several commands:

```bash
# Parse and display Cursor rules structure
python3 cursorrules_parser.py parse <cursorrules-file>

# Convert to gptme lesson format
python3 cursorrules_parser.py to-lesson <cursorrules-file> [output-file]

# Convert gptme lesson back to Cursor rules
python3 cursorrules_parser.py from-lesson <lesson-file> [output-file]
```

### Format Differences

**Cursor Rules** (`.cursorrules`):
- Single monolithic file (under 500 lines recommended)
- Project-specific coding standards
- Complete code examples
- File pattern-based activation

**gptme Lessons** (`.md` files):
- Two-file architecture (30-50 line primary + detailed companion)
- Behavioral guidance and failure prevention
- Concise patterns with links to full context
- Keyword and tool-based inclusion

### Example Conversion

Given a `.cursorrules` file:

```markdown
# Overview
TypeScript/React project with strict type safety

## Rules

### Always
- Use TypeScript strict mode
- Prefer functional components
- Use hooks for state management

### Auto Attached: tsx!
- Include prop type definitions
- Use React.FC for component types
```

Converts to gptme lesson format:

```yaml
---
match:
  keywords: [typescript, react, component]
---

# TypeScript React Best Practices

## Overview
TypeScript/React project with strict type safety

## Rules

### Strict Type Safety
- Use TypeScript strict mode in all files
- Enable all strict compiler options

### Component Patterns
- Prefer functional components over class components
- Use React hooks for state management
- Include explicit prop type definitions
- Use React.FC for consistent component types

... (continued)
```

### Best Practices

1. **Project-Local Lessons**: Place converted Cursor rules in `.gptme/lessons/` within your project
2. **Keep Both**: Maintain both `.cursorrules` for Cursor editor and `.gptme/lessons/` for gptme
3. **Sync Manually**: Update both when coding standards change
4. **Use Appropriate Format**: Cursor rules for prescriptive standards, gptme lessons for behavioral guidance

### Troubleshooting

**Issue**: Conversion tool not found
```bash
# Solution: Clone gptme-contrib
git clone https://github.com/gptme/gptme-contrib
cd gptme-contrib/cursorrules
```

**Issue**: Lessons not being included
```bash
# Check if .gptme/lessons/ directory exists
ls .gptme/lessons/

# Verify lesson format
head .gptme/lessons/project-rules.md

# Check logs for "Indexed n lessons" message
gptme --verbose
```

### Further Resources

- [Cursor Rules Documentation](https://cursor.com/docs/context/rules)
- [gptme-contrib Repository](https://github.com/gptme/gptme-contrib)
- [Lesson System Architecture](https://github.com/gptme/gptme/blob/master/docs/lessons/README.md)
