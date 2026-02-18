# Design: Generalized Elicitation

**Issue**: [#1121](https://github.com/gptme/gptme/issues/1121)
**Author**: Bob
**Date**: 2026-02-18
**Status**: Implemented (Phase 1 - Foundation)
**Depends on**: [#1105](https://github.com/gptme/gptme/pull/1105) (hook-based confirmations, merged)

## Background

"Elicitation" is the process by which an agent requests structured input from the user.
The term is now widely used in agent contexts: Linear, Claude Code, OpenCode, and Lovable
all have variations on structured user input beyond simple text prompts.

This design builds on the hook-based confirmation system merged in #1105, extending it
to a generalized elicitation architecture.

## Implementation Status

| Phase | Component | Status |
|-------|-----------|--------|
| 1 | `ElicitationRequest` dataclass | ✅ Complete |
| 1 | `ElicitationResponse` dataclass | ✅ Complete |
| 1 | `FormField` dataclass | ✅ Complete |
| 1 | `HookType.ELICIT` hook type | ✅ Complete |
| 1 | `ElicitationHook` protocol | ✅ Complete |
| 1 | `elicit()` function with hook dispatch | ✅ Complete |
| 1 | CLI elicitation handler | ✅ Complete |
| 1 | `elicit` tool for agent use | ✅ Complete |
| 1 | 29 tests passing | ✅ Complete |
| 2 | Server (WebUI) elicitation handler | ❌ Planned |
| 2 | MCP elicitation client support | ❌ Planned |
| 3 | Secret storage (keyring integration) | ❌ Planned |

## Core Concepts

### Elicitation vs Confirmation

| Aspect | Confirmation (tool.confirm) | Elicitation (elicit) |
|--------|---------------------------|---------------------|
| Initiator | Tool execution (system) | Agent (LLM) |
| Purpose | Approve/skip/edit tool run | Collect information |
| Types | yes/no/edit | text, choice, secret, form, ... |
| LLM context | Tool details shown | Response MAY be hidden |
| Result | `ConfirmationResult` | `ElicitationResponse` |

### Elicitation vs MCP Elicitation

gptme's native elicitation (this design) is **agent-initiated**: the gptme agent
asks the user for input needed to complete a task.

MCP's `elicitation/requestInput` is **server-initiated**: an external MCP server
asks gptme (as MCP client) to collect input on its behalf. This is a separate flow
handled in `gptme/mcp/client.py`. They are different:

- gptme elicitation: gptme agent → user
- MCP elicitation: MCP server → gptme (client) → user

## Architecture

### Request Types

```python
@dataclass
class ElicitationRequest:
    type: Literal["text", "choice", "multi_choice", "secret", "confirmation", "form"]
    prompt: str
    options: list[str] | None = None    # for choice/multi_choice
    fields: list[FormField] | None = None  # for form
    default: str | None = None
    sensitive: bool = False             # always True for "secret"
    description: str | None = None
```

### Response

```python
@dataclass
class ElicitationResponse:
    value: str | None = None       # for text/secret/confirmation/form(JSON)
    values: list[str] | None = None  # for multi_choice
    cancelled: bool = False
    sensitive: bool = False        # if True, don't add to LLM context
```

### Hook System

Elicitation uses the same hook machinery as confirmations (`HookType.ELICIT`).
Hooks are registered with priority (higher = tried first), and return `None` to
fall through to the next hook.

```
Agent calls elicit()
    → Try HookType.ELICIT hooks in priority order
    → First non-None response wins
    → If all None and stdin is TTY: fallback to cli_elicit()
    → If all None and non-TTY: return ElicitationResponse.cancel()
```

### Secret Handling (Key Use Case)

The most valuable elicitation type is `secret`. Secrets (API keys, passwords) must:
1. Not appear in terminal output (use `getpass`)
2. Not be added to LLM conversation context
3. Be stored/used securely by the agent

The `elicit` tool handles this: when the elicitation type is `secret`, the tool
yields a `"User provided secret value (not shown)"` message instead of the actual
value. The agent receives the secret via the response and must handle it explicitly
(e.g. write to an env file, pass to a tool that stores credentials).

```python
# Agent workflow for API key setup:
response = elicit(ElicitationRequest(type="secret", prompt="Enter your OpenAI API key:"))
# response.value contains the key, but conversation message says "(not shown)"
# Agent stores it securely:
write_to_env_file("OPENAI_API_KEY", response.value)
```

## Implemented Files

### `gptme/hooks/elicitation.py`
- `ElicitationRequest`, `ElicitationResponse`, `FormField` dataclasses
- `ElicitationHook` protocol
- `cli_elicit()` - CLI handler using `getpass` for secrets, `questionary` for rich UIs
- `elicit()` - Main function with hook dispatch and CLI fallback
- `register_cli_elicitation_hook()` - Helper to register CLI hook

### `gptme/tools/elicit.py`
- `tool_elicit` - ToolSpec for the `elicit` tool
- `parse_elicitation_spec()` - Parse JSON elicitation spec from agent
- `execute_elicit()` - Execute elicitation and format response for LLM context

### `gptme/hooks/__init__.py`
- Added `HookType.ELICIT = "elicit"`
- Added imports for elicitation types
- Added type overload for `register_hook` with `HookType.ELICIT`
- Added `ElicitationHook` to `HookFunc` union

## Agent Usage

The agent uses the `elicit` tool with a JSON spec:

````markdown
```elicit
{
  "type": "secret",
  "prompt": "Enter your OpenAI API key:",
  "description": "Required for the OpenAI integration. Will not be logged."
}
```
````

```json
{
  "type": "choice",
  "prompt": "Which framework should we use?",
  "options": ["FastAPI", "Django", "Flask"]
}
```

```json
{
  "type": "form",
  "prompt": "New project setup:",
  "fields": [
    {"name": "name", "prompt": "Project name?", "type": "text"},
    {"name": "language", "prompt": "Language?", "type": "choice", "options": ["python", "typescript"]},
    {"name": "tests", "prompt": "Include tests?", "type": "boolean"}
  ]
}
```

## Server Integration (Phase 2)

For the WebUI, a server elicitation hook would:
1. Receive the `ElicitationRequest` from the agent
2. Emit an SSE event to the browser client with the request
3. Block until the client responds via a new HTTP endpoint
4. Return the response to the agent

This follows the same pattern as `server_confirm.py` for tool confirmations.

```python
# Planned server_elicit.py
def server_elicit_hook(request: ElicitationRequest) -> ElicitationResponse | None:
    session_id = current_session_id.get()
    if not session_id:
        return None  # Fall through to CLI or cancel

    elicit_id = str(uuid.uuid4())
    pending = register_pending_elicitation(elicit_id, request)

    emit_sse_event("elicitation_requested", {
        "elicit_id": elicit_id,
        "type": request.type,
        "prompt": request.prompt,
        "options": request.options,
    })

    # Block until client responds
    pending.event.wait(timeout=300)
    return pending.response
```

## Open Questions

1. **Secret storage mechanism**: Should gptme provide a built-in keyring integration?
   The current approach (agent stores explicitly) is flexible but requires agent awareness.

2. **MCP elicitation bridge**: Should gptme's `elicit()` also trigger MCP elicitation
   when an MCP server has registered a handler? MCP's `elicitation/requestInput` would
   make this possible.

3. **Timeout handling**: Should `ElicitationRequest` include a timeout? The server
   hook would need it; CLI can rely on Ctrl+C.

4. **Should `confirmation` type in elicitation unify with `tool.confirm`?**
   Current design: separate (confirmations are about tool execution, elicitations
   are about information collection). Could be unified later if patterns emerge.

## Comparison with Other Harnesses

| Feature | gptme | Claude Code | OpenCode | Lovable |
|---------|-------|-------------|----------|---------|
| Text input | ✅ | ✅ | ✅ | ✅ |
| Single choice | ✅ (choice tool) | ✅ | ✅ | ? |
| Multi-choice | ✅ (new) | ✅ | ? | ? |
| Form | ✅ (form tool + new) | ✅ | ? | ? |
| Secret/hidden | ✅ (new) | ✅ | ? | ? |
| Confirmation | ✅ (tool.confirm) | ✅ | ✅ | ✅ |
| Server/Web UI | 🚧 (planned) | ✅ | ✅ | ✅ |
| MCP bridge | 🚧 (planned) | ? | ? | ? |
