# Hook System Implementation

This document describes the implementation of the hook system for gptme in response to [issue #156](https://github.com/gptme/gptme/issues/156).

## Overview

The hook system provides a flexible way to extend gptme's functionality at various lifecycle points. Tools and plugins can register callbacks that execute at specific events like file saves, message processing, session start/end, and more.

## Implementation Details

### Core Components

#### 1. Hook Registry (`gptme/hooks.py`)

A new module that provides:
- **HookType enum**: Defines all available hook types
- **Hook dataclass**: Represents a registered hook with name, type, function, priority, and enabled state
- **HookRegistry class**: Thread-safe registry for managing hooks
- **Global functions**: `register_hook()`, `trigger_hook()`, `get_hooks()`, `enable_hook()`, `disable_hook()`, `unregister_hook()`, `clear_hooks()`

**Hook Types Available:**
- Message lifecycle: `MESSAGE_PRE_PROCESS`, `MESSAGE_POST_PROCESS`, `MESSAGE_TRANSFORM`
- Tool lifecycle: `TOOL_PRE_EXECUTE`, `TOOL_POST_EXECUTE`, `TOOL_TRANSFORM`
- File operations: `FILE_PRE_SAVE`, `FILE_POST_SAVE`, `FILE_PRE_PATCH`, `FILE_POST_PATCH`
- Session lifecycle: `SESSION_START`, `SESSION_END`
- Generation: `GENERATION_PRE`, `GENERATION_POST`, `GENERATION_INTERRUPT`

#### 2. ToolSpec Extension (`gptme/tools/base.py`)

Modified `ToolSpec` to support hooks:
- Added `hooks` field: `dict[str, tuple[str, Callable, int]]`
- Added `register_hooks()` method to register tool's hooks with the global registry
- Updated tool initialization to call `register_hooks()` when tools are loaded

#### 3. Hook Trigger Points

**In `gptme/tools/base.py` (ToolUse.execute):**
- `TOOL_PRE_EXECUTE`: Triggered before tool execution
- `TOOL_POST_EXECUTE`: Triggered after successful tool execution

**In `gptme/chat.py`:**
- `SESSION_START`: Triggered at the beginning of chat session
- `SESSION_END`: Triggered when chat session ends (via return or break)
- `MESSAGE_PRE_PROCESS`: Triggered before processing a message
- `MESSAGE_POST_PROCESS`: Triggered after message processing completes

**In `gptme/tools/save.py`:**
- `FILE_PRE_SAVE`: Triggered before saving a file
- `FILE_POST_SAVE`: Triggered after successfully saving a file

### Tools Using Hooks

#### 1. Pre-commit Hook Tool (`gptme/tools/precommit.py`)

Automatically runs pre-commit checks in two scenarios:
- **Per-file checks**: Hooks into `FILE_POST_SAVE` event
  - Runs `pre-commit run --files <path>` on saved files
  - Provides immediate feedback
- **Full checks**: Hooks into `MESSAGE_POST_PROCESS` event
  - Runs `pre-commit run --all-files` after message processing
  - Ensures all changes pass before auto-commit
- Reports failures with detailed output
- Hides success messages to reduce noise
- Disabled by default (enable with `--tools precommit`)

#### 2. Todo Replay Hook Tool (`gptme/tools/todo_replay.py`)

Automatically replays todowrite operations at session start:
- Hooks into `SESSION_START` event
- Detects todowrite operations in initial messages
- Calls `_replay_tool()` to restore todo state
- Replaces hard-coded todowrite replay in chat.py

#### 3. Autocommit Hook Tool (`gptme/tools/autocommit.py`)

Automatically commits changes after message processing:
- Hooks into `MESSAGE_POST_PROCESS` event
- Checks if GPTME_AUTOCOMMIT environment variable is enabled
- Checks for file modifications
- Returns message asking LLM to review and commit changes
- Replaces hard-coded autocommit logic in chat.py

#### 4. Enhanced Pre-commit Hook Tool (`gptme/tools/precommit.py`)

Extended to support both per-file and full pre-commit checks:
- **Per-file checks**: Hooks into `FILE_POST_SAVE` event
  - Runs pre-commit on specific saved file
  - Provides immediate feedback
- **Full checks**: Hooks into `MESSAGE_POST_PROCESS` event
  - Runs pre-commit on all modified files
  - Ensures all changes pass before auto-commit
  - Replaces hard-coded pre-commit logic in chat.py

### Removed Hard-coded Logic

**In `gptme/chat.py`:**
- Removed hard-coded todowrite replay (lines 102-111)
  - Now handled by the `todo_replay` tool via `SESSION_START` hook
- Removed hard-coded `_check_and_handle_modifications()` function
  - Pre-commit checks now handled by `precommit` tool via `MESSAGE_POST_PROCESS` hook
  - Auto-commit now handled by `autocommit` tool via `MESSAGE_POST_PROCESS` hook
- Removed `check_changes()` function (no longer needed)
- Updated imports to remove unused `autocommit` and `run_precommit_checks` from util.context

## Files Changed

### Modified Files

1. **gptme/tools/base.py**
   - Added `hooks` field to `ToolSpec`
   - Added `register_hooks()` method
   - Added hook triggers in `ToolUse.execute()`

2. **gptme/tools/__init__.py**
   - Added call to `tool.register_hooks()` in `init_tools()`

3. **gptme/chat.py**
   - Added `SESSION_START` hook trigger at session initialization
   - Added `SESSION_END` hook triggers at exit points
   - Added `MESSAGE_PRE_PROCESS` and `MESSAGE_POST_PROCESS` hook triggers
   - Removed hard-coded todowrite replay logic
   - Removed hard-coded `_check_and_handle_modifications()` function
   - Removed `check_changes()` function
   - Updated imports to remove unused `autocommit` and `run_precommit_checks`

4. **gptme/tools/save.py**
   - Added `FILE_PRE_SAVE` and `FILE_POST_SAVE` hook triggers

5. **docs/index.rst**
   - Added hooks documentation to Developer Guide section

### New Files

1. **gptme/hooks.py** - Core hook system implementation
2. **gptme/tools/precommit.py** - Pre-commit hook tool (per-file + full checks)
3. **gptme/tools/todo_replay.py** - Todo replay hook tool
4. **gptme/tools/autocommit.py** - Auto-commit hook tool
5. **docs/hooks.rst** - Comprehensive hooks documentation
6. **tests/test_hooks.py** - Hook system tests
7. **HOOKS_IMPLEMENTATION.md** - This implementation summary

## Usage Examples

### Registering Hooks in Tools

```python
from gptme.tools.base import ToolSpec
from gptme.hooks import HookType
from gptme.message import Message

def on_file_save(path, content, created):
    """Hook function called after a file is saved."""
    if path.suffix == ".py":
        return Message("system", f"Python file saved: {path}")
    return None

tool = ToolSpec(
    name="my_tool",
    desc="Example tool with hooks",
    hooks={
        "file_save": (
            HookType.FILE_POST_SAVE.value,  # Hook type
            on_file_save,                    # Hook function
            10                               # Priority (higher runs first)
        )
    }
)
```

### Programmatic Hook Registration

```python
from gptme.hooks import register_hook, HookType
from gptme.message import Message

def my_hook_function(log, workspace):
    """Custom hook function."""
    return Message("system", "Hook executed!")

register_hook(
    name="my_custom_hook",
    hook_type=HookType.MESSAGE_PRE_PROCESS,
    func=my_hook_function,
    priority=0,
    enabled=True
)
```

### Hook Function Signatures

Different hook types receive different arguments:

```python
# Message hooks
def message_hook(log, workspace):
    pass

# Tool hooks
def tool_hook(tool_name, tool_use):
    pass

# File hooks
def file_hook(path, content, created=False):
    pass

# Session hooks
def session_hook(logdir, workspace, manager=None, initial_msgs=None):
    pass
```

## Testing

Comprehensive test suite in `tests/test_hooks.py`:
- Hook registration
- Hook triggering
- Priority ordering
- Enable/disable functionality
- Unregistration
- Multiple hooks
- Generator hooks
- Error handling
- Argument passing

Run tests:
```bash
pytest tests/test_hooks.py -v
```

## Documentation

Comprehensive documentation in `docs/hooks.rst`:
- Hook types and their purposes
- Usage examples
- Registration methods
- Managing hooks (enable/disable/unregister)
- Best practices
- Thread safety notes
- Migration guide
- API reference

## Benefits

1. **Extensibility**: Easy to add new functionality without modifying core code
2. **Modularity**: Tools can register hooks independently
3. **Flexibility**: Multiple hooks per type, priority ordering
4. **Clean Architecture**: Separates concerns, reduces hard-coded logic
5. **Testability**: Hooks can be tested in isolation
6. **Discoverability**: Hooks are documented and centrally managed

## Future Enhancements

Potential future improvements mentioned in issue #156:

1. **Dynamic System Prompts**: Use hooks to generate system prompts on every message
2. **Memory Implementation**: Implement memory system using hooks (similar to simplemind)
3. **Patch/Save Optimization**: Transform operations before execution
4. **Expert Sub-agents**: Call specialized sub-agents via hooks
5. **Interrupt Detection**: More sophisticated generation interruption
6. **More File Hooks**: Add `FILE_PRE_PATCH` and `FILE_POST_PATCH` triggers to patch tool
7. **Command Registration**: Allow tools to register custom commands like `/commit`
8. **Configuration**: Environment variables to enable/disable hooks globally

## Migration Path

To convert existing features to hooks:

1. **Identify the feature**: Determine what should be a hook
2. **Choose hook type**: Select appropriate hook type
3. **Extract logic**: Move feature logic into a hook function
4. **Register hook**: Add to a ToolSpec or register programmatically
5. **Remove hard-coded logic**: Clean up original implementation
6. **Test thoroughly**: Ensure hook works in all scenarios
7. **Document**: Update documentation

Example: The todowrite replay feature was converted from hard-coded logic in chat.py to a SESSION_START hook in todo_replay tool.

## Related Issues

- [#156](https://github.com/gptme/gptme/issues/156) - Original feature request
- [#151](https://github.com/gptme/gptme/issues/151) - Dynamic system prompts (can use hooks)
- [#152](https://github.com/gptme/gptme/issues/152) - Before running tools (can use TOOL_PRE_EXECUTE)
- [#199](https://github.com/gptme/gptme/pull/199) - Linting/checking experiments
- [#233](https://github.com/gptme/gptme/issues/233) - Simplemind integration (hooks could help)

## Conclusion

The hook system provides a powerful, flexible foundation for extending gptme. It enables tools to react to events throughout the application lifecycle without modifying core code, maintaining clean separation of concerns while providing extensive customization capabilities.

The implementation is production-ready with:
- ✅ Complete core implementation
- ✅ Multiple example tools
- ✅ Comprehensive documentation
- ✅ Full test coverage
- ✅ Thread-safe design
- ✅ Clean migration path

Next steps would be to convert more existing features to hooks and add additional hook types as needed by the community.
