# Skills System

> **Note**: Skills are **lightweight knowledge bundles** using Anthropic's format. For deep runtime integration (hooks, custom tools, commands), use [plugins](../plugins.rst) instead.

The skills system extends gptme's lesson system to support bundled tools, scripts, and workflows inspired by Claude's Skills system and Cursor's rules system.

## Overview

**Skills** are enhanced lessons that bundle:
- Instructional content (like lessons)
- Executable scripts and utilities
- Dependencies and setup requirements

Skills complement lessons by providing **executable components** alongside guidance.

## Skill vs. Lesson vs. Plugin

| Feature | Lesson | Skill | Plugin |
|---------|--------|-------|--------|
| Purpose | Guidance and patterns | Executable workflows | Deep runtime integration |
| Content | Instructions, examples | Instructions + scripts | Tools, hooks, commands |
| Scripts | None | Bundled helper scripts | Via custom tools |
| Dependencies | None | Explicit package requirements | Python package dependencies |
| Hooks | No | No | Yes |
| Custom Tools | No | No | Yes |
| Format | Markdown | Anthropic YAML | Python package |

**When to use**:
- **Lesson**: Teaching patterns, best practices, tool usage
- **Skill**: Providing reusable scripts, automated workflows (lightweight)
- **Plugin**: Runtime hooks, custom tools, deep gptme integration (see [plugins](../plugins.rst))

## Skill Format


Skills use YAML frontmatter following Anthropic's format:

```yaml
---
name: skill-name
description: Brief description of what the skill does and when to use it
---

# Skill Title

Skill description and usage instructions...
```

**Note**: Dependencies are specified in `requirements.txt`, and bundled scripts are placed in the same directory as `SKILL.md`.

Skill description and usage instructions...
```

## Directory Structure

Skills are organized parallel to lessons:

gptme/
└── lessons/           # Unified knowledge tree
    ├── tools/        # Tool-specific lessons
    ├── patterns/     # General patterns
    ├── workflows/    # Workflow lessons
    └── skills/       # Skills (Anthropic format)
        └── python-repl/
            ├── SKILL.md
            ├── python_helpers.py
            └── requirements.txt

## Creating Skills

### 1. Design the Skill

Identify:
- What workflow or automation does it provide?
- What scripts/utilities are needed?
- What dependencies are required?

### 2. Create Skill Directory

Create a directory under `gptme/lessons/skills/skill-name/` with these files:

**SKILL.md** (Anthropic format):
```yaml
---
name: skill-name
description: Brief description of what the skill does
---

# Skill Title

## Overview
Detailed description and use cases.

## Bundled Scripts
Describe each included script.

## Usage Patterns
Show common usage examples.

## Dependencies
List required packages (detailed in requirements.txt).
```

**requirements.txt**:

```text
# List of required packages
numpy
pandas
```

### 3. Create Bundled Scripts

Create helper scripts in the same directory as the skill:

```python
#!/usr/bin/env python3
"""Helper script for skill."""

def helper_function():
    """Does something useful."""
    pass
```

### 4. Test the Skill

```python
from gptme.lessons.parser import parse_lesson
from pathlib import Path

# Parse skill from unified lessons tree
skill = parse_lesson(Path("gptme/lessons/skills/my-skill/SKILL.md"))
assert skill.metadata.name == "my-skill"
assert skill.metadata.description
```


### Deep Integration with Plugins

**For runtime integration (hooks, custom tools, commands), use the [Plugin System](../plugins.rst).**

Skills are lightweight knowledge bundles that remain simple. For deeper integration with gptme's runtime:

- **Hooks**: Register lifecycle callbacks (see [Hooks Documentation](../hooks.rst))
- **Custom Tools**: Add new capabilities (see [Plugin Tools](../plugins.rst#creating-a-plugin))
- **Commands**: Add CLI commands (see [Plugin Commands](../plugins.rst#plugin-command-modules))

**Example**: For a skill that needs hooks, create a plugin instead:

```python
# In a plugin: my_plugin/hooks/setup.py
from gptme.hooks import HookType, register_hook

def setup_environment(logdir, workspace, initial_msgs):
    """Initialize environment at session start."""
    # Your hook logic here
    yield

def register():
    register_hook("my_plugin.setup", HookType.SESSION_START, setup_environment)
```

See [Plugin System Documentation](../plugins.rst) for complete examples.

## Use Cases

### Data Analysis Skill
- Bundles pandas, numpy helpers
- Auto-imports common libraries
- Provides data inspection utilities
- Includes plotting helpers

### Testing Skill
- Bundles pytest configuration
- Provides test utilities
- Auto-discovers tests
- Formats test reports

### API Development Skill
- Bundles FastAPI templates
- Provides auth helpers
- Includes validation utilities
- Auto-generates OpenAPI docs

## Integration with Lessons

Skills complement lessons:
- **Lesson teaches** the pattern
- **Skill provides** the tooling

Example:
- Lesson: `lessons/patterns/testing.md` - Testing best practices
- Skill: `skills/testing-skill.md` - Bundled pytest utilities

## Roadmap

### Current Status (Phase 4.1)
- ✅ Parser support for skills metadata
- ✅ Example skill with bundled scripts
- ✅ Documentation
- ✅ Hook system (available in [plugins](../plugins.rst), not skills)

### Future Work (Phase 4.2+)
- [ ] Dependency management for skills
- [ ] Script bundling and loading for skills
- [ ] Skills CLI commands
- [ ] Skills discovery and listing

**Note**: For runtime integration (hooks, custom tools, commands), see [Plugin System](../plugins.rst). Skills remain lightweight knowledge bundles.

## Related

- [Lesson System](../lessons) - Core knowledge system
- [Plugin System](../plugins.rst) - For hooks, custom tools, and deep integration
- [Hooks Documentation](../hooks.rst) - Lifecycle callbacks (plugins only)
- [Issue #686](https://github.com/gptme/gptme/issues/686) - Phase 4: Skills Integration
- [Claude Skills](https://simonwillison.net/2025/Oct/10/claude-skills/) - Inspiration
