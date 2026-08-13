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

This matters because a 2026 benchmark study (arXiv:2608.06370, "The Bitter Lesson
of Tool Calling") found that PTC **matches or exceeds** JSON-schema tool calling on
11/14 models, and critically, PTC maintains stable accuracy under **context rot**
(long sessions with accumulated tool history) while JSON-schema accuracy degrades
~2.3%. gptme's long-running autonomous sessions sit squarely in the regime where
this stability advantage compounds.

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
tool-call responses are parsed.** The LLM adapters (`gptme/llm/llm_openai.py`,
`gptme/llm/llm_anthropic.py`) convert each `ToolSpec` to a JSON-schema tool
definition via `_spec2tool` and send these definitions to the provider alongside
the conversation. The provider returns a structured tool call
(e.g., `@shell(abc-123): {"cmd": "ls"}`); gptme parses the JSON arguments and
dispatches via the same `ToolSpec.execute` path.

Schema handling for this mode lives entirely in `gptme/llm/`, not in
`gptme/tools/` — the `ToolSpec.execute` interface itself remains code-based.

### MCP adapter

`gptme/tools/mcp_adapter.py` speaks the MCP protocol, which uses JSON Schema to
describe external MCP server tools. This schema is used to **generate human-readable
instructions** for the model (not to gate dispatch) and to validate MCP server
responses. gptme's own tools are never dispatched via JSON schema.

---

## Audit: No JSON-Schema Dispatch Paths in `gptme/tools/`

Audit run 2026-08-13, commit range: `origin/master`.

```bash
grep -r "json\|schema\|Json\|Schema" gptme/tools/ \
  --include="*.py" | grep -v __pycache__ | grep -v test
```

**Findings by file**:

| File | JSON usage | Dispatch path? |
|------|-----------|----------------|
| `base.py` | Argument serialisation (`_to_json`, `_to_params`) and "tool"-format parsing | ❌ Not dispatch — argument mapping only |
| `mcp_adapter.py` | MCP protocol JSON schema for external server tools | ❌ Not gptme tool dispatch |
| `patch_anchored.py` | JSON array of edit operations (tool's own content format) | ❌ Not dispatch |
| `vent.py` | JSONL output to friction ledger | ❌ Not dispatch |
| `progress.py` | JSONL output to progress log | ❌ Not dispatch |
| `shell.py` | context-savings JSONL log | ❌ Not dispatch |
| `restart.py` | `--output-schema` CLI flag for structured subagent output | ❌ Not tool dispatch |

**Verdict**: No JSON-schema dispatch paths in `gptme/tools/`. For markdown and XML
formats, `ToolSpec.execute(code, args, kwargs)` receives the raw code block content.
JSON-schema handling for provider-native tool mode lives in `gptme/llm/` (the
`_spec2tool` functions in `llm_openai.py` and `llm_anthropic.py`), which is outside
this `gptme/tools/` audit scope.

---

## Why PTC Wins at Long Context (Context Rot)

arXiv:2608.06370v1 benchmarked 14 models on BFCL v4 across three conditions:

1. **Simple**: isolated tool call, no history
2. **Distracted**: irrelevant tool history in context
3. **Rotted**: many prior tool calls of the correct type in context

JSON-schema tool calling degrades ~2.3% average accuracy under context rot across
models. PTC holds steady. The paper's hypothesis: JSON schemas are longer and
more repetitive in context; accumulated tool history crowds out the actual task.
Code blocks are shorter, more compositional, and less sensitive to sequence length.

**Implication for gptme**: autonomous sessions routinely accumulate 50–200 tool
calls in a single conversation. This is exactly the regime where JSON-schema
accuracy degrades. gptme's markdown-first PTC interface is architecturally
positioned to remain stable where JSON-schema approaches falter.

---

## References

- arXiv:2608.06370v1 — "The Bitter Lesson of Tool Calling" (August 2026)
  - BFCL v4 benchmark, 14 models, context-rot stability analysis
  - Key finding: PTC matches/exceeds JSON in 11/14 models; ~2.3% JSON degradation under context rot
- `gptme/tools/base.py` — `ToolSpec`, `ToolUse`, dispatch paths, `ToolFormat`
- `gptme/tools/python.py` — IPython execution backend
- `gptme/tools/shell.py` — subprocess bash execution backend
- `gptme/tools/mcp_adapter.py` — MCP protocol bridge (external tool JSON schema)
- Issue [#3540](https://github.com/gptme/gptme/issues/3540) — audit request
