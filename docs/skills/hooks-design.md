# Skills Hook System Design (Phase 4.2)

**Date**: 2025-11-25
**Status**: Implementation In Progress

## Overview

Skills can define hooks that execute at specific points in the skill lifecycle. This document describes the hook system architecture and design decisions.

## Hook Types

1. **pre_execute**: Before skill's bundled scripts run
2. **post_execute**: After skill's bundled scripts run
3. **on_error**: When skill execution fails
4. **pre_context**: Before skill is added to context
5. **post_context**: After skill is added to context

## Hook Definition

Hooks are defined in skill YAML frontmatter:

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

Hook files are Python scripts relative to the skill directory.

## Hook Context

Each hook receives a `HookContext` object with:
- `skill`: The Lesson object representing the skill
- `message`: Current message being processed (optional)
- `conversation`: Current conversation context (optional)
- `tools`: Available tools
- `config`: gptme configuration

## Design Decisions

### 1. Hook Priority (Multiple skills with same hook)
**Decision**: Execute in registration order (FIFO)
**Rationale**: Simple, predictable, allows manual ordering by skill loading order
**Future**: Add priority field in metadata if needed

### 2. Hook Failure Handling
**Decision**: Log error but continue with other hooks
**Rationale**: One failing skill shouldn't break entire system
**Implementation**: Try-except around each hook call, log with logger.error()

### 3. Script Namespace
**Decision**: Shared namespace per skill, isolated between skills
**Rationale**: Allow helper functions within skill, prevent conflicts between skills
**Implementation**: Each skill gets its own module namespace via importlib

### 4. Dependency Conflicts
**Decision**: Document as best practice, no automatic resolution
**Rationale**: Keep Phase 4.2 focused on core functionality
**Future**: Consider virtual environments per skill in Phase 4.3+

### 5. Security
**Decision**: No sandboxing in Phase 4.2
**Rationale**: Skills are trusted code (like lessons), focus on functionality first
**Documentation**: Add security warnings and best practices
**Future**: Consider sandboxing in future phase if needed

## Implementation Plan

1. Update `gptme/lessons/parser.py`:
   - Add `hooks: dict[str, str]` to LessonMetadata
   - Parse hooks from YAML frontmatter

2. Create `gptme/lessons/hooks.py`:
   - HookContext dataclass
   - HookManager class with register/execute methods
   - Hook loading and execution logic

3. Add hook execution points:
   - In tool execution flow (pre_execute, post_execute, on_error)
   - In context building (pre_context, post_context)

4. Add tests:
   - Test hook registration
   - Test hook execution
   - Test error handling
   - Test isolation between skills

5. Update documentation:
   - Add hooks section to docs/skills/README.md
   - Add example skills with hooks
   - Document best practices

## Example Hook

```python
# hooks/pre_execute.py
from gptme.lessons.hooks import HookContext

def execute(context: HookContext) -> None:
    """Pre-execute hook example."""
    print(f"Preparing to execute skill: {context.skill.title}")
    # Setup code here
```

## Future Enhancements

- Hook priority system
- Async hook support
- Hook timeouts
- Virtual environments per skill
- Sandboxed execution
- Hook composition/chaining
