# Server Hooks Analysis

## Summary

The user's concern is **valid** - some hooks don't work in the server because the server doesn't use `chat.py`, which triggers several lifecycle hooks.

## Hooks That Work in Server

These hooks are triggered in tools and LLM code, so they work in both CLI and server:

### Tool Execution Hooks (triggered in `tools/base.py`)
- ✅ **TOOL_PRE_EXECUTE** - Before executing any tool
- ✅ **TOOL_POST_EXECUTE** - After executing any tool

### File Operation Hooks (triggered in `tools/save.py`)
- ✅ **FILE_PRE_SAVE** - Before saving a file
- ✅ **FILE_POST_SAVE** - After saving a file
- ⚠️ **FILE_PRE_PATCH** - Defined but not used yet
- ⚠️ **FILE_POST_PATCH** - Defined but not used yet

### Generation Hooks (triggered in `llm/__init__.py`)
- ✅ **GENERATION_PRE** - Before generating response

## Hooks That NOW Work in Server (After Implementation)

These hooks were previously CLI-only but have been implemented in the server:

### Session Lifecycle (now in `api_v2_sessions.py`)
- ✅ **SESSION_START** - Triggered for new conversations (0 assistant messages)
- ✅ **SESSION_END** - Triggered when last session for a conversation is removed

### Message Processing (now in `api_v2_sessions.py`)
- ✅ **MESSAGE_PRE_PROCESS** - Triggered before generating a response
- ✅ **MESSAGE_POST_PROCESS** - Triggered after persisting the assistant message

### Chat Loop Control (still CLI-only)
- ❌ **LOOP_CONTINUE** - Decide whether to continue the chat loop
  - Not applicable in server context (server is event-driven, not loop-based)

## Root Cause

The server has its own message processing flow in `gptme/server/api_v2_sessions.py`:
- `step()` function (line 182) handles generation and tool detection
- `start_tool_execution()` function (line 380) handles tool execution
- Neither function calls the message processing or session lifecycle hooks

## Impact

Tools that register hooks will work differently in the server vs CLI:

### Example: autocommit tool
The autocommit tool uses `MESSAGE_POST_PROCESS` hook to suggest commits. This hook:
- ✅ **Works in CLI** - triggered after each message
- ❌ **Doesn't work in server** - never triggered

### Example: precommit tool
The precommit tool uses `FILE_POST_SAVE` hook for validation. This hook:
- ✅ **Works in both** - triggered when files are saved via the save tool

## Implementation Details

### Changes Made (Branch: dev/server-hooks-support)

#### 1. Added Hook Import
- Imported `HookType` and `trigger_hook` in `api_v2_sessions.py`

#### 2. SESSION_START Hook
- **Location**: `step()` function after loading manager
- **Trigger condition**: When `len(assistant_messages) == 0` (new conversation)
- **Parameters**: `logdir`, `workspace`, `initial_msgs`
- **Behavior**: Hook messages are appended to log and notified to clients

#### 3. MESSAGE_PRE_PROCESS Hook
- **Location**: `step()` function after preparing messages, before generation
- **Trigger condition**: Every step before generation
- **Parameters**: `manager`
- **Behavior**: Hook messages are appended to log and notified to clients

#### 4. MESSAGE_POST_PROCESS Hook
- **Location**: `step()` function after persisting assistant message
- **Trigger condition**: Every step after assistant responds
- **Parameters**: `manager`
- **Behavior**: Hook messages are appended to log and notified to clients

#### 5. SESSION_END Hook
- **Location**: `SessionManager.remove_session()` method
- **Trigger condition**: When last session for a conversation is removed
- **Parameters**: `manager`
- **Behavior**: Hook messages are appended to log (no notification needed)

## Recommendations

1. ✅ **Completed**: Implement MESSAGE_PRE_PROCESS/MESSAGE_POST_PROCESS in server
2. ✅ **Completed**: Implement SESSION_START/SESSION_END in server
3. **Next**: Test all hooks work correctly in both CLI and server
4. **Next**: Document which hooks work in server vs CLI in main docs
5. **Future**: Consider if LOOP_CONTINUE makes sense in server context

## Files to Check

- Hook definitions: `gptme/hooks.py`
- Tool execution: `gptme/tools/base.py` (TOOL_* hooks work in server)
- File operations: `gptme/tools/save.py` (FILE_* hooks work in server)
- Message processing: `gptme/chat.py` (MESSAGE_* hooks don't work in server)
- Server logic: `gptme/server/api_v2_sessions.py` (needs hook integration)
