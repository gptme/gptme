# Skills System

The skills system extends gptme's lesson system to support bundled tools, scripts, and workflows inspired by Claude's Skills system and Cursor's rules system.

## Overview

**Skills** are enhanced lessons that bundle:
- Instructional content (like lessons)
- Executable scripts and utilities
- Dependencies and setup requirements

Skills complement lessons by providing **executable components** alongside guidance.

## Skill vs. Lesson

| Feature | Lesson | Skill |
|---------|--------|-------|
| Purpose | Guidance and patterns | Executable workflows |
| Content | Instructions, examples | Instructions + scripts |
| Scripts | None | Bundled helper scripts |
| Dependencies | None | Explicit package requirements |

**When to use**:
- **Lesson**: Teaching patterns, best practices, tool usage
- **Skill**: Providing reusable scripts, automated workflows, integrated tooling

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

### Current Status (Phase 4.2)
- ✅ Parser support for skills metadata (Phase 4.1)
- ✅ Example skill with bundled scripts (Phase 4.1)
- ✅ Skills documentation (Phase 4.1)
- ✅ Hook system implementation (Phase 4.2)
- ✅ Hook execution and error handling (Phase 4.2)
- ✅ Example skill with hooks (Phase 4.2)

### Future Work (Phase 4.3+)
- [ ] Dependency validation and checking
- [ ] Script bundling and automatic loading
- [ ] Skills CLI commands
- [ ] Skills discovery and listing
- [ ] Hook integration points in execution flow

## Related

- [Lesson System](../lessons)
- [Issue #686](https://github.com/gptme/gptme/issues/686) - Phase 4: Skills Integration
- [Claude Skills](https://simonwillison.net/2025/Oct/10/claude-skills/) - Inspiration

## Hook System (Phase 4.2)

Skills can define hooks that execute at specific points in their lifecycle.

### Available Hooks

- **pre_execute**: Runs before the skill's bundled scripts execute
- **post_execute**: Runs after successful execution
- **on_error**: Runs when an error occurs during execution
- **pre_context**: Runs before the skill is added to context
- **post_context**: Runs after the skill is added to context

### Defining Hooks

Hooks are defined in the skill's YAML frontmatter:

```yaml
---
name: my-skill
description: Example skill with hooks
hooks:
  pre_execute: hooks/pre_execute.py
  post_execute: hooks/post_execute.py
  on_error: hooks/on_error.py
---
```

Hook scripts are Python files relative to the skill directory.

### Hook Script Structure

Each hook script must define an `execute()` function:

```python
"""Pre-execute hook for my-skill."""

import logging
from gptme.lessons.hooks import HookContext

logger = logging.getLogger(__name__)

def execute(context: HookContext) -> None:
    """Pre-execute hook implementation.

    Args:
        context: Hook execution context with skill, message, conversation, etc.
    """
    logger.info(f"Setting up skill: {context.skill.title}")

    # Hook implementation here
    # - Check dependencies
    # - Initialize resources
    # - Set up environment
```

### Hook Context

Hooks receive a `HookContext` object with:

- `skill`: The Lesson object representing the skill
- `message`: Current message being processed (optional)
- `conversation`: Current conversation context (optional)
- `tools`: Available tools (optional)
- `config`: gptme configuration (optional)
- `extra`: Additional context data (optional)

### Example Skill with Hooks

See `gptme/lessons/skills/example-hooks/` for a complete example demonstrating all hook types.

### Hook Behavior

**Execution Order**: Hooks execute in registration order (FIFO) when multiple skills register the same hook type.

**Error Handling**: If a hook raises an exception:
- The error is logged
- Execution continues with remaining hooks
- The error does not stop the skill from executing

**Isolation**: Each hook script runs in its own namespace, isolated from other skills' hooks.

### Use Cases

**Pre-execute hooks**:
- Validate dependencies
- Set up environment variables
- Initialize resources
- Log execution start

**Post-execute hooks**:
- Clean up resources
- Save results
- Update state
- Log execution completion

**Error hooks**:
- Handle errors gracefully
- Clean up partial resources
- Notify monitoring systems
- Log detailed error information

**Context hooks**:
- Modify skill content before inclusion
- Add dynamic information
- Filter or enhance instructions

### Design Decisions

For details on hook system design decisions (priority, failure handling, namespaces, etc.), see the [Hooks Design](hooks-design) document.

## Further Reading

.. toctree::
   :maxdepth: 1

   hooks-design
