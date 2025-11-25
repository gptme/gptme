# Skills System

The skills system extends gptme's lesson system to support bundled tools, scripts, and workflows inspired by Claude's Skills system and Cursor's rules system.

## Overview

**Skills** are enhanced lessons that bundle:
- Instructional content (like lessons)
- Executable scripts and utilities
- Dependencies and setup requirements
- Hook points for automation

Skills complement lessons by providing **executable components** alongside guidance.

## Skill vs. Lesson

| Feature | Lesson | Skill |
|---------|--------|-------|
| Purpose | Guidance and patterns | Executable workflows |
| Content | Instructions, examples | Instructions + scripts |
| Scripts | None | Bundled helper scripts |
| Dependencies | None | Explicit package requirements |
| Hooks | None | Pre/post execution hooks |

**When to use**:
- **Lesson**: Teaching patterns, best practices, tool usage
- **Skill**: Providing reusable scripts, automated workflows, integrated tooling

## Skill Format

Skills use the same YAML frontmatter as lessons with additional fields:

```yaml
---
type: skill  # Distinguishes from lessons
status: active
match:
  keywords: [python, data]
  tools: [ipython]
scripts:
  - helper_script.py
  - utilities.py
dependencies:
  - numpy
  - pandas
hooks:
  - pre_execute
  - post_execute
---

# Skill Title

Skill description and usage instructions...
```

### Metadata Fields

#### Required
- `type`: Must be "skill" (defaults to "lesson")
- `status`: active, automated, deprecated, or archived
- `match`: Trigger keywords and tools

#### Optional
- `scripts`: List of bundled script files (relative to skill directory)
- `dependencies`: Required Python packages
- `hooks`: Execution hooks (pre_execute, post_execute)

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
- Where should hooks run?

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

```txt
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

## Hook System

Skills support hooks for automation:

### Pre-Execute Hook
Runs before skill execution:
- Validate environment
- Install dependencies
- Set up imports
- Configure settings

### Post-Execute Hook
Runs after skill execution:
- Capture output
- Format results
- Log metrics
- Clean up resources

### Implementation

Hook implementation is planned for future phases:

```python
# Future API (conceptual)
from gptme.skills import register_hook

@register_hook("pre_execute")
def setup_python_env(skill):
    """Install dependencies and set up imports."""
    for dep in skill.metadata.dependencies:
        ensure_installed(dep)
```

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

### Future Work (Phase 4.2+)
- [ ] Hook system implementation
- [ ] Dependency management
- [ ] Script bundling and loading
- [ ] Skills CLI commands
- [ ] Skills discovery and listing

## Related

- [Lesson System](../lessons)
- [Issue #686](https://github.com/gptme/gptme/issues/686) - Phase 4: Skills Integration
- [Claude Skills](https://simonwillison.net/2025/Oct/10/claude-skills/) - Inspiration