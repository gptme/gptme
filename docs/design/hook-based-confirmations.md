# Design: Hook-Based Tool Confirmations

**Issue**: [#1104](https://github.com/gptme/gptme/issues/1104)
**Author**: Bob
**Date**: 2026-01-10
**Status**: In Progress

## Implementation Status

| Phase | Component | Status |
|-------|-----------|--------|
| 1 | HookType.TOOL_CONFIRM | ✅ Complete |
| 1 | ConfirmationResult dataclass | ✅ Complete |
| 1 | ToolConfirmHook protocol | ✅ Complete |
| 1 | get_confirmation() function | ✅ Complete |
| 2 | cli_confirm_hook | ✅ Complete |
| 2 | auto_confirm_hook | ✅ Complete |
| 2 | Hook registration in chat.py | ✅ Complete |
| 2 | confirm_bridge utilities | ✅ Complete |
| 2 | confirm_func integration | ✅ Complete |
| 3 | server_confirm_hook | ✅ Complete |
| 3 | HTTP endpoint integration | ✅ Complete |
| 3 | Tests (32 passing) | ✅ Complete |
| 4 | Server context vars for SSE | ✅ Complete |
| 4 | Server hook registration | ✅ Complete |
| 4 | V1 API hook-aware confirm_func | ✅ Complete |
| 5 | Tool migration | ❌ Reverted (see notes) |
| 6 | Simplification & cleanup | ✅ Complete |
| 6.1 | Consolidate preview printing | ✅ Complete |
| 6.2 | Centralize auto-confirm state | ✅ Complete |
| 6.3 | Unify server auto-confirm | ✅ Complete |
| 6.4 | Consolidate help text | ✅ Complete |
| 7 | Remove ask_execute fallback | ✅ Complete |
| 7.1 | Simplify confirm_func | ✅ Complete |
| 7.2 | Move CLI hook to init_hooks | ✅ Complete |
| 7.3 | Use contextvars for auto-confirm | ✅ Complete |
| 8 | Tool auto-approve via ToolSpec hooks | ✅ Complete |
| 8.1 | Hook fall-through support | ✅ Complete |
| 8.2 | Shell allowlist hook | ✅ Complete |
| 8.3 | Tests for fall-through & allowlist | ✅ Complete |

**Current state**: Phases 1-4, 6, 7, 8 complete. Phase 5 was reverted.

**Implemented**:
- `confirm_func` in `chat.py` always uses hooks (no `ask_execute` fallback)
- `confirm_func` in `api.py` (v1) uses hooks when available, falling back to auto-confirm
- Server's HTTP endpoint resolves hook-based confirmations via `_resolve_hook_confirmation`
- Server hook now emits SSE events and blocks until client responds via HTTP endpoint
- Context vars (`current_conversation_id`, `current_session_id`) provide session context to hooks

**Phase 5 Reversion Notes**:
The Phase 5 "tool migration" was reverted because:
1. It added ~88 lines without removing any (violated simplification goal)
2. Tools were creating ToolUse objects just to pass to confirmation - this is redundant since ToolUse already exists at the `ToolUse.execute()` level
3. The `_execute_with_hook_confirmation()` helper duplicated logic from `execute_with_confirmation()`

The hook system works correctly through the `confirm_func` bridge without requiring tools to create ToolUse objects.

**Architecture notes**:
- V1 API: Uses hook-aware confirm_func, auto-confirms when no context vars set (legacy behavior)
- V2 API: Uses separate `pending_tools` mechanism + hook resolution for HTTP confirmations
- CLI: Uses hook-aware confirm_func, routes through cli_confirm_hook when registered
- Hooks receive confirmation requests via `make_confirm_func_from_hooks()` bridge

**Phase 6.1 Notes** (Completed):
Consolidated duplicate `_print_preview` in cli_confirm.py by importing shared `print_preview`
from ask_execute.py. This reduced cli_confirm.py by 10 lines (245 → 235) and eliminates
duplicate preview logic.

**Phase 6.4 Notes** (Completed):
Extracted shared `print_confirmation_help()` function in ask_execute.py. Both
ask_execute and cli_confirm_hook now use this shared function instead of
maintaining duplicate help text. Reduced cli_confirm.py by 19 lines.

**Phase 6.2-6.3 Notes** (Completed):
Centralized auto-confirm state in `confirm.py` with unified functions:
- `set_auto_confirm(count)` - Set auto-confirm (count or infinite)
- `reset_auto_confirm()` - Reset to defaults
- `check_auto_confirm()` - Check and decrement (returns tuple)
- `is_auto_confirm_active()` - Check without decrementing

Both `cli_confirm.py` and `ask_execute.py` now use this centralized state instead of
maintaining their own duplicate globals. Server auto-confirm is also unified -
`server_confirm_hook` now checks centralized state first before checking session context.

**Phase 7 Notes** (Completed):
Removed ask_execute fallback from chat.py per Erik's suggestion:
- confirm_func now always uses hooks via make_confirm_func_from_hooks()
- CLI hook registration moved into init_hooks() via hook_allowlist parameter
- Auto-confirm state converted to ContextVars for thread safety in server mode
- When no_confirm=True, no CLI hook is registered, so get_confirmation() auto-confirms

**Phase 8 Notes** (Completed):
Tools register their own auto-approve hooks via ToolSpec.hooks per Erik's suggestion:
- Modified `get_confirmation()` to support fall-through: hooks returning None pass to next hook
- Hooks are tried in priority order (highest first), first non-None result wins
- Updated `ToolConfirmHook` protocol: now returns `ConfirmationResult | None`
- Shell tool registers `shell_allowlist_hook` with priority 10 (higher than CLI hook at 0)
- Shell allowlist hook auto-confirms allowlisted commands, returns None for others
- This keeps ToolSpec clean (no new fields) while enabling tool-specific auto-approve
- Tests added: 3 fall-through tests + 5 shell allowlist tests (27 total passing)

Example usage for other tools:
```python
def my_tool_auto_approve(tool_use, preview=None, workspace=None):
    """Auto-approve safe operations, fall through for others."""
    if is_safe(tool_use):
        return ConfirmationResult.confirm()
    return None  # Fall through to CLI/server hook

tool = ToolSpec(
    name="my_tool",
    hooks={
        "auto_approve": ("tool_confirm", my_tool_auto_approve, 10),
    },
    ...
)
```

**Next steps**:
- ✅ Phase 6.1-6.4: Consolidation complete
- ✅ Phase 7: Remove ask_execute fallback
- ✅ Phase 8: Tool auto-approve via ToolSpec hooks
- Phase 6.5: Document hook API for custom confirmation backends
- Phase 6.6: Add examples for new backends (GUI, Discord bot)
- Future: Consider moving confirmation to ToolUse.execute()

## Problem Statement

gptme currently has two separate implementations for tool confirmation:

1. **CLI (`ask_execute.py`)**: Interactive terminal-based confirmation with rich features
2. **Server V2 (`api_v2_sessions.py`)**: SSE event-based confirmation with pending tool queue

These implementations are not harmonized:
- CLI uses `ask_execute()` called directly from tools via `ConfirmFunc`
- Server V2 uses `pending_tools` dict with `ToolExecution` state machine
- Server V1 has no real confirmation support (always auto-confirms)
- No shared abstraction for confirmation logic

## Goals

1. **Harmonize** CLI and Server confirmation implementations
2. **Leverage hooks** for extensibility and clean separation
3. **Maintain** existing functionality (edit, copy, auto-confirm)
4. **Simplify** tool implementations by removing confirmation boilerplate
5. **Enable** new confirmation backends (e.g., GUI, Discord bot)

## Current Architecture

### CLI Flow

```text
User/Tool → execute_with_confirmation() → ask_execute() → User Input → Execute
                                              ↓
                                        print_preview()
                                              ↓
                                    editable/copiable state
```

### Server V2 Flow

```text
Tool Execute → Store in pending_tools → SSE Event (tool_pending) → Client Decides
                                                                         ↓
                                                              /api/v2/.../tool/confirm
                                                                         ↓
                                                              Execute or Skip
```

### Key Differences

| Aspect | CLI | Server V2 |
|--------|-----|-----------|
| Blocking | Synchronous (blocks thread) | Async (event-based) |
| Input | Terminal prompt | HTTP endpoint |
| Features | edit, copy, auto | confirm, edit, skip, auto |
| State | Global variables | Session object |
| Notification | Bell sound | SSE event |

## Proposed Design

### Core Concept: `tool.confirm` Hook

Introduce a new hook type `tool.confirm` that handles the confirmation decision:

```python
class HookType(str, Enum):
    # Existing hooks
    TOOL_EXECUTE_PRE = "tool.execute.pre"
    TOOL_EXECUTE_POST = "tool.execute.post"

    # New confirmation hook
    TOOL_CONFIRM = "tool.confirm"
```

### Confirmation Protocol

The `tool.confirm` hook follows a request-response protocol:

1. **Request Phase**: System triggers `tool.confirm` with tool details
2. **Decision Phase**: Hook implementation gathers user/client decision
3. **Response Phase**: Hook yields a `ConfirmationResult`

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class ConfirmationResult:
    """Result of a tool confirmation request."""

    action: Literal["confirm", "skip", "edit"]
    edited_content: str | None = None
    auto_confirm_remaining: int = 0
```

### Hook Protocol

```python
from typing import Protocol, Generator
from pathlib import Path

class ToolConfirmHook(Protocol):
    """Hook for tool confirmation decisions."""

    def __call__(
        self,
        tooluse: "ToolUse",
        preview: str | None,
        workspace: Path | None,
    ) -> Generator[ConfirmationResult, None, None]:
        """Request confirmation for tool execution.

        Args:
            tooluse: The tool about to be executed
            preview: Optional preview content for display
            workspace: Workspace directory path

        Yields:
            ConfirmationResult with the user's decision
        """
        pass
```

### Implementation Architecture

```text
                     ┌─────────────────────────────────────┐
                     │         ToolUse.execute()           │
                     └─────────────────┬───────────────────┘
                                       │
                                       ▼
                     ┌─────────────────────────────────────┐
                     │    trigger_hook(TOOL_CONFIRM)       │
                     └─────────────────┬───────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
              ▼                        ▼                        ▼
    ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
    │ CLI Confirm Hook│      │Server Confirm   │      │ Auto Confirm    │
    │ (terminal input)│      │Hook (SSE/HTTP)  │      │ Hook (always)   │
    └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
             │                        │                        │
             ▼                        ▼                        ▼
    ┌─────────────────────────────────────────────────────────────────────┐
    │                      ConfirmationResult                              │
    └─────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                     ┌─────────────────────────────────────┐
                     │      Execute or Skip Tool           │
                     └─────────────────────────────────────┘
```

### Hook Implementations

#### 1. CLI Confirmation Hook

```python
def cli_confirm_hook(
    tooluse: ToolUse,
    preview: str | None,
    workspace: Path | None,
) -> Generator[ConfirmationResult, None, None]:
    """CLI-based confirmation using terminal input."""

    # Show preview if available
    if preview:
        print_preview(preview, tooluse.tool, copy=True)

    # Make content editable
    if tooluse.content:
        set_editable_text(tooluse.content, get_extension(tooluse))

    # Get user decision via terminal prompt
    confirmed = ask_execute(f"Execute {tooluse.tool}?")

    if confirmed:
        edited = get_editable_text() if editable else None
        was_edited = edited != tooluse.content if edited else False
        yield ConfirmationResult(
            action="edit" if was_edited else "confirm",
            edited_content=edited if was_edited else None,
        )
    else:
        yield ConfirmationResult(action="skip")
```

#### 2. Server Confirmation Hook

```python
def server_confirm_hook(
    tooluse: ToolUse,
    preview: str | None,
    workspace: Path | None,
) -> Generator[ConfirmationResult, None, None]:
    """Server-based confirmation using SSE events."""

    session = get_current_session()

    # Check auto-confirm
    if session.auto_confirm_count > 0:
        session.auto_confirm_count -= 1
        yield ConfirmationResult(action="confirm")
        return

    # Create pending tool entry
    tool_id = str(uuid.uuid4())
    session.pending_tools[tool_id] = ToolExecution(
        tooluse=tooluse,
        status=ToolStatus.PENDING,
    )

    # Emit SSE event
    emit_event("tool_pending", {
        "tool_id": tool_id,
        "tool": tooluse.tool,
        "content": tooluse.content,
        "preview": preview,
    })

    # Wait for client decision (via HTTP endpoint)
    result = wait_for_confirmation(tool_id)
    yield result
```

#### 3. Auto-Confirm Hook (Non-Interactive Mode)

```python
def auto_confirm_hook(
    tooluse: ToolUse,
    preview: str | None,
    workspace: Path | None,
) -> Generator[ConfirmationResult, None, None]:
    """Always confirms - for non-interactive/autonomous mode."""
    yield ConfirmationResult(action="confirm")
```

### Integration Points

#### Tool Execution Flow

```python
# In tools/base.py - ToolUse.execute()

def execute(self, confirm: ConfirmFunc) -> Generator[Message, None, None]:
    # Trigger confirmation hook
    confirm_results = list(trigger_hook(
        HookType.TOOL_CONFIRM,
        tooluse=self,
        preview=self.get_preview(),
        workspace=get_workspace(),
    ))

    if not confirm_results:
        # No confirmation hook registered - fall back to confirm function
        if not confirm(f"Execute {self.tool}?"):
            yield Message("system", "Aborted")
            return
        result = ConfirmationResult(action="confirm")
    else:
        result = confirm_results[0]

    # Handle result
    if result.action == "skip":
        yield Message("system", "Operation skipped by user")
        return

    if result.action == "edit" and result.edited_content:
        self.content = result.edited_content

    # Proceed with execution
    yield from self._do_execute()
```

#### Hook Registration

```python
# In gptme/main.py or gptme/chat.py

def init_confirmation_hooks(interactive: bool, server_mode: bool):
    """Register appropriate confirmation hook based on mode."""

    if server_mode:
        register_hook(
            name="server_confirm",
            hook_type=HookType.TOOL_CONFIRM,
            func=server_confirm_hook,
            priority=100,
        )
    elif interactive:
        register_hook(
            name="cli_confirm",
            hook_type=HookType.TOOL_CONFIRM,
            func=cli_confirm_hook,
            priority=100,
        )
    else:
        register_hook(
            name="auto_confirm",
            hook_type=HookType.TOOL_CONFIRM,
            func=auto_confirm_hook,
            priority=100,
        )
```

## Evaluation Dimensions

### Dimension 1: Code Simplification

**Criteria**: Does this reduce complexity in tool implementations?

| Score | Description |
|-------|-------------|
| 1 | Increases complexity |
| 2 | No change |
| 3 | Minor simplification |
| 4 | Moderate simplification |
| 5 | Major simplification |

**Current Assessment: 4/5**

Rationale:
- Tools no longer need to handle confirmation logic directly
- `execute_with_confirmation()` helper can be simplified or deprecated
- Single point of confirmation logic vs scattered across tools
- Minor complexity added in hook registration

### Dimension 2: Extensibility

**Criteria**: How easy is it to add new confirmation backends?

| Score | Description |
|-------|-------------|
| 1 | Requires core changes |
| 2 | Complex integration |
| 3 | Moderate effort |
| 4 | Simple plugin |
| 5 | Trivial addition |

**Current Assessment: 5/5**

Rationale:
- New backends just register a hook function
- No core code changes needed
- Examples: Discord bot, GUI, mobile app, voice confirmation
- Clear protocol makes implementation straightforward

### Dimension 3: Backward Compatibility

**Criteria**: Does this maintain existing behavior and APIs?

| Score | Description |
|-------|-------------|
| 1 | Breaking changes, migration required |
| 2 | Breaking changes, partial migration |
| 3 | Deprecation warnings, works with changes |
| 4 | Fully backward compatible with deprecations |
| 5 | Fully backward compatible, no changes needed |

**Current Assessment: 4/5**

Rationale:
- `ConfirmFunc` type can still work (fallback when no hook)
- `ask_execute()` still functions (wrapped by CLI hook)
- Server V2 API unchanged externally
- Internal refactoring required for `execute_with_confirmation()`

### Dimension 4: Testability

**Criteria**: How testable is the new design?

| Score | Description |
|-------|-------------|
| 1 | Untestable / requires manual testing |
| 2 | Difficult to test |
| 3 | Moderate test effort |
| 4 | Easy to unit test |
| 5 | Excellent testability with mocks |

**Current Assessment: 5/5**

Rationale:
- Hooks are pure functions that can be mocked
- `ConfirmationResult` is a simple dataclass
- Can test each hook implementation independently
- Can test tool execution with different hook configurations

### Dimension 5: Server Harmonization

**Criteria**: Does this improve CLI/Server code sharing?

| Score | Description |
|-------|-------------|
| 1 | More divergence |
| 2 | No change |
| 3 | Minor sharing |
| 4 | Significant sharing |
| 5 | Full harmonization |

**Current Assessment: 4/5**

Rationale:
- Same protocol for both CLI and Server
- Same `ConfirmationResult` type
- Tool code doesn't need to know which environment
- Server still needs SSE/HTTP infrastructure (inherent)

### Dimension 6: Performance Impact

**Criteria**: Does this affect performance?

| Score | Description |
|-------|-------------|
| 1 | Significant slowdown |
| 2 | Noticeable slowdown |
| 3 | Minor impact |
| 4 | Negligible impact |
| 5 | No impact or improvement |

**Current Assessment: 5/5**

Rationale:
- Hook dispatch is O(1) lookup
- No additional I/O or computation
- Existing confirmation logic just moves to hook
- Could potentially improve by reducing redundant preview generation

## Overall Evaluation

| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Code Simplification | 4 | 20% | 0.80 |
| Extensibility | 5 | 25% | 1.25 |
| Backward Compatibility | 4 | 20% | 0.80 |
| Testability | 5 | 15% | 0.75 |
| Server Harmonization | 4 | 15% | 0.60 |
| Performance Impact | 5 | 5% | 0.25 |
| **Total** | | | **4.45/5** |

## Implementation Plan

### Phase 1: Foundation (1-2 days)
1. Add `HookType.TOOL_CONFIRM` enum value
2. Add `ToolConfirmHook` protocol
3. Add `ConfirmationResult` dataclass
4. Update hook type overloads

### Phase 2: CLI Implementation (2-3 days)
1. Create `gptme/hooks/cli_confirm.py`
2. Refactor `ask_execute.py` to be callable by hook
3. Register CLI hook in interactive mode
4. Test with existing CLI flows

### Phase 3: Server Implementation (2-3 days)
1. Create `gptme/hooks/server_confirm.py`
2. Integrate with `api_v2_sessions.py` pending_tools
3. Register Server hook in server mode
4. Test with existing Server V2 flows

### Phase 4: Tool Migration (3-5 days)
1. Update `ToolUse.execute()` to use confirmation hook
2. Simplify `execute_with_confirmation()` usage
3. Migrate tools one by one
4. Add deprecation warnings for direct `ask_execute` usage

### Phase 5: Documentation & Cleanup (1-2 days)
1. Document hook API
2. Add examples for custom confirmation backends
3. Remove deprecated code paths
4. Update tests

## Risks and Mitigations

### Risk 1: Async/Sync Mismatch
**Risk**: Server needs async, CLI is sync
**Mitigation**: Hook protocol uses generators which work for both; Server hook uses threading.Event for blocking

### Risk 2: State Management
**Risk**: Auto-confirm count, editable state are currently global
**Mitigation**: Move state into hook context or use ContextVars

### Risk 3: Migration Complexity
**Risk**: Many tools use `execute_with_confirmation`
**Mitigation**: Phased migration with backward compatibility layer

## Open Questions

1. **Priority System**: Should multiple confirmation hooks be allowed? (e.g., logging + confirmation)
2. **Timeout**: Should there be a configurable timeout for confirmation?
3. **Preview Protocol**: Should preview generation be standardized across tools?
4. **State Location**: Where should auto-confirm count live in server mode?

## Alternatives Considered

### Alternative 1: Use Existing TOOL_EXECUTE_PRE
**Rejected**: PRE hook doesn't have a response mechanism; would need to modify hook system fundamentally.

### Alternative 2: Middleware Pattern
**Rejected**: More complex than hooks; would require new abstraction layer.

### Alternative 3: Event System
**Rejected**: Overkill for this use case; hooks are simpler and already exist.

## Conclusion

The hook-based confirmation design provides a clean, extensible solution that:
- Harmonizes CLI and Server implementations
- Maintains backward compatibility
- Enables new confirmation backends
- Simplifies tool implementations

**Recommendation**: Proceed with implementation starting from Phase 1.
