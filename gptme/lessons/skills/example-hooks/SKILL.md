---
name: example-hooks
description: Example skill demonstrating hook system usage
hooks:
  pre_execute: hooks/pre_execute.py
  post_execute: hooks/post_execute.py
  on_error: hooks/on_error.py
---

# Example Hooks Skill

This skill demonstrates how to use the hook system to extend skill functionality.

## Hooks

This skill defines three hooks:

- **pre_execute**: Runs before the skill's bundled scripts execute
- **post_execute**: Runs after successful execution
- **on_error**: Runs when an error occurs during execution

## Usage

Skills can use hooks to:
- Set up prerequisites before execution
- Clean up resources after execution
- Handle errors gracefully
- Log execution details
- Integrate with external systems

## Example Hook Functions

Each hook receives a `HookContext` object with:
- `skill`: The Lesson object representing the skill
- `message`: Current message being processed (optional)
- `conversation`: Current conversation context (optional)
- `tools`: Available tools (optional)
- `config`: gptme configuration (optional)

See the hook files in `hooks/` for implementation examples.
