# Design: Programmatic Tool Calling (PTC) Interface

**Date**: 2026-08-13
**Status**: Stable — this documents the existing architecture

## Summary

gptme's primary tool interface is **Programmatic Tool Calling (PTC)**:
the model outputs *executable code* in fenced code blocks, and gptme runs it
directly via Python (IPython) or the shell. For the default `markdown` and `xml`
formats, no JSON schemas are handed to the model and no JSON-structured tool-call
responses are parsed.

gptme also supports a **provider-native tool mode** (the `"tool"` format) for
OpenAI and Anthropic APIs. In this mode, `ToolSpec` parameters are converted to
JSON-schema tool definitions and sent to the provider; the provider returns
structured tool calls which gptme parses before dispatching to `ToolSpec.execute`.
This path trades the context-rot resilience of PTC for compatibility with
provider-side tool routing.

This matters because gptme's long-running autonomous sessions accumulate 50–200 tool
calls in a single conversation. JSON-schema tool definitions are verbose and repetitive
in context; accumulated provider-native tool history crowds out the actual task. PTC
code blocks are shorter, more compositional, and less sensitive to context length.
gptme's markdown-first design is architecturally positioned to remain stable in the
long-context regime where JSON-schema approaches are most likely to degrade.

---

## Dispatch Paths

### Primary: Markdown code blocks (PTC)

The default and primary tool format is `"markdown"`. The model writes:

````
```python
print("hello")
```
````

gptme parses the fenced code block, identifies the language tag as a tool name
(`python`, `shell`, `save`, `patch`, …), and calls the registered `ToolSpec.execute`
function with the block content as `code`. For `python`, this runs the code via
IPython's `run_cell()`; for `shell`, via `subprocess.Popen` with a stateful bash
shell. **There is no JSON parsing in this path.** The content is code; it runs as
code.

### Secondary: XML code blocks

The `"xml"` format wraps the same code in XML tags:

```xml
<tool-use>
<python>
print("hello")
</python>
</tool-use>
```

Dispatch is identical — `tool.execute(content, args, kwargs)` — with the code block
content passing straight through. Still PTC.

### Tertiary: "tool" format (provider-native tool mode)

The `"tool"` format (`@name(id): {...}`) supports providers that expose a native
tool-use API (e.g., OpenAI Responses API, Anthropic tool use).

**This is the one path where JSON schemas are sent to the model and structured
tool-call responses are parsed.** The implementation spans two layers:

- **`gptme/llm/`** (schema → provider): `_spec2tool` in `llm_openai.py` /
  `llm_anthropic.py` converts each `ToolSpec`'s `.parameters` to a JSON-schema
  tool definition and sends it to the provider alongside the conversation.
- **`gptme/tools/base.py`** (provider → dispatch): `ToolUse.iter_from_content`
  with `active_format == "tool"` parses the `@name(id): {...}` response using
  `json_repair.loads`, extracts `kwargs`, and yields a `ToolUse` that then calls
  `ToolSpec.execute`.

The `ToolSpec.execute` interface itself is code-based in all formats; the JSON
layer is the serialisation envelope for provider-native calls, handled in
`gptme/llm/` (outbound schemas) and `gptme/tools/base.py` (inbound argument
parsing).

### MCP adapter

`gptme/tools/mcp_adapter.py` speaks the MCP protocol, which uses JSON Schema to
describe external MCP server tools. This schema is used to **generate human-readable
instructions** for the model (not to gate dispatch) and to validate MCP server
responses. gptme's own tools are never dispatched via JSON schema.

---

## Audit: No JSON-Schema Dispatch Paths in `gptme/tools/`

Audit run 2026-08-16, commit `66057c6e8`, all Python files recursively under
`gptme/tools/`.

```bash
grep -r "json\|schema\|Json\|Schema" gptme/tools/ \
  --include="*.py" | grep -v __pycache__ | grep -v test
```

**Findings by file**:

| File | JSON usage | Dispatch path? |
|------|-----------|----------------|
| `base.py` | `_to_json`/`_to_params` serialisation + `ToolUse.iter_from_content` "tool"-format JSON parser (`json_repair.loads`) | ⚠️ Part of provider-native path — parses `@name(id): {...}` responses |
| `mcp_adapter.py` | MCP protocol JSON schema for external server tools | ❌ Not gptme tool dispatch |
| `mcp.py` | MCP protocol JSON parsing for server commands and arguments | ❌ Not dispatch |
| `patch_anchored.py` | JSON array of edit operations (tool's own content format) | ❌ Not dispatch |
| `patch_many.py` | JSON array of edit operations (multi-patch content format) | ❌ Not dispatch |
| `elicit.py` | JSON for form-spec parsing (`json.loads(code)`) and response formatting | ❌ Not dispatch — parses the tool's own content, not tool selection |
| `form.py` | JSON for form result output formatting | ❌ Not dispatch |
| `gh.py` | JSON parsing of `gh` CLI output (PR details, check runs) | ❌ Not dispatch |
| `computer.py`, `computer_semantic.py` | JSON for action telemetry and semantic-target payloads | ❌ Not dispatch |
| `pruner.py` | JSON parsing of pruner payload in message content | ❌ Not dispatch |
| `autocompact/engine.py` | Conversation JSONL path and message-position bookkeeping | ❌ Not dispatch |
| `subagent/api.py` | JSONL cancellation protocol and structured-output schema forwarding | ❌ Not dispatch |
| `subagent/batch.py` | Structured-output JSON decoding and optional Pydantic result validation | ❌ Not dispatch — subagent result content |
| `subagent/control.py`, `subagent/execution.py` | JSONL control/progress protocol and structured-output plumbing | ❌ Not dispatch |
| `subagent/hooks.py`, `subagent/types.py` | JSON Schema prompt generation plus JSON syntax normalization for structured subagent results | ❌ Not dispatch — validates the subagent's result content |
| `vent.py` | JSONL output to friction ledger | ❌ Not dispatch |
| `progress.py` | JSONL output to progress log | ❌ Not dispatch |
| `shell.py` | JSONL context-savings log | ❌ Not dispatch |
| `restart.py` | `--output-schema` CLI flag for structured subagent output | ❌ Not tool dispatch |
| `chats.py`, `rag.py` | JSONL conversation file reading | ❌ Not dispatch |
| `_browser_playwright.py`, `browser.py`, `_browser_thread.py` | Browser session state JSON | ❌ Not dispatch |

**Verdict**: Two distinct things are worth separating here:

1. **Schema-definition dispatch** (using a JSON schema to *select* which tool to invoke):
   never occurs in any path. The `@name(id): {...}` format in the provider-native path
   names the tool directly; the schema is only sent *outbound* to the provider to help
   it structure its response, not used inbound to route calls.

2. **JSON argument parsing after tool selection**: `base.py` **does** parse JSON from
   provider responses via `ToolUse.iter_from_content` with `active_format == "tool"`.
   This `json_repair.loads` call is the trust boundary for untrusted provider data —
   **security reviewers should treat this as in-scope** even though it is not schema-guided
   dispatch.

For markdown and XML formats, `ToolSpec.execute(code, args, kwargs)` receives raw code block
content with no JSON parsing at any layer. The ⚠️ on `base.py` above applies only to the
`"tool"` format (provider-native mode); the outbound schema half lives in `gptme/llm/`.

---

## Why PTC Favours Long Context (Context Rot Argument)

JSON-schema tool calling sends the full parameter schema for every available tool on
every turn. In a long autonomous session this adds significant token overhead that
repeats with every message. PTC code blocks carry no per-turn schema payload: the
model writes code, gptme runs it — the only context each block occupies is the code
itself.

The concern compounds under **context rot**: when prior tool-call history accumulates
(50–200 entries in a typical autonomous run), JSON-schema responses grow proportionally
because each prior turn's structured `tool_call` / `tool_result` pair is preserved in
the conversation. PTC history is just markdown code blocks and their output — shorter,
compositional, and no more repetitive than the code itself.

**Empirical evidence**: A 2026 benchmark study, arXiv:2608.06370v1 (*"The Bitter
Lesson of Tool Calling"*), compared PTC with native JSON tool calling across 14
models on BFCL v4. PTC matched or exceeded JSON on 11/14 models, with the GPT-5.6
family improving by up to 10.6 percentage points. In a separate context-flooding
ablation (31 entries per condition), the authors expanded the available tool set
from only task-relevant schemas to 128 schemas with unrelated decoys. Mean JSON
accuracy fell 2.3 percentage points, while mean PTC accuracy rose 5.5 points.

That ablation tests schema flooding in a single benchmark query, not accumulated
multi-turn history, so it does not directly measure gptme's 50–200-call sessions.
It supports the narrower claim that PTC is robust when irrelevant tool definitions
inflate the context; the extrapolation to long-running sessions above remains an
architectural rationale rather than a measured result.

---

## References

- `gptme/tools/base.py` — `ToolSpec`, `ToolUse`, dispatch paths, `ToolFormat`
- `gptme/tools/python.py` — IPython execution backend
- `gptme/tools/shell.py` — subprocess bash execution backend
- `gptme/tools/mcp_adapter.py` — MCP protocol bridge (external tool JSON schema)
- [arXiv:2608.06370v1](https://arxiv.org/abs/2608.06370v1) — *"The Bitter Lesson of Tool Calling"* (August 2026): BFCL v4 benchmark across 14 models; its context-flooding ablation compares task-relevant schemas with 128 schemas containing unrelated decoys
- Issue [#3540](https://github.com/gptme/gptme/issues/3540) — audit request
