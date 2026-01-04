# Glossary

This document defines key terminology used throughout the gptme codebase.

## Conversational Concepts

(turn)=
### Turn
A complete conversational exchange between the user and the assistant.

A turn consists of:
1. A user message (input)
2. All assistant responses and tool executions until no more tools are runnable
3. Any system messages generated during processing

In the context of LLMs, "turns" denote the explicit conversational exchanges between a user and the model. A single turn may contain multiple [steps](#step).

**Code reference**: The `_process_user_msg()` function in `gptme/chat.py` processes a complete turn.

(step)=
### Step
A single cycle of LLM generation and tool execution within a turn.

A step consists of:
1. Pre-process hooks execution
2. LLM response generation
3. Tool execution (if tools are present in the response)

In the context of LLMs, "steps" generally refer to an internal reasoning process or a sequence of actions an agent takes to solve a problem. Multiple steps may occur within a single [turn](#turn).

**Code reference**: The `step()` function in `gptme/chat.py` performs one step.

### Message Processing
The complete handling of a user message, including all steps until no more tools need to run.

**Hooks behavior**:
- `MESSAGE_PRE_PROCESS`: Fires before each [step](#step)
- `MESSAGE_POST_PROCESS`: Fires once after all steps complete (i.e., once per [turn](#turn))

For the complete list of hook types and their lifecycle, see the [Hooks documentation](hooks.rst).

## Context and Memory

### Context Window
The maximum number of tokens a model can process in a single request. This includes all messages, tool definitions, and system prompts.

### Prompt Cache
A mechanism to cache and reuse previously processed context, reducing token costs for repeated prefixes. Cache invalidation occurs when the cached portion changes.

### Token
A unit of text processed by the model. Tokens are typically sub-word units (e.g., "unhappy" → "un" + "happy").

## Tool Concepts

### Tool
A function that the assistant can execute to perform actions like reading files, running commands, or making API calls.

### ToolUse
A parsed representation of a tool invocation found in an assistant's response.

### Runnable Tool
A tool that can be executed in the current context. Some tools may be defined but not runnable (e.g., disabled or context-restricted).

## Session Concepts

### Log / LogManager
The conversation history and its management system. Stores all messages exchanged in a session.

### Workspace
The directory context in which gptme operates. Tools like file operations are scoped to the workspace.

## Configuration

### Model
The LLM backend used for generation (e.g., `openai/gpt-4`, `anthropic/claude-3`).

### Tool Format
How tools are presented to the model: `"markdown"` (tool blocks in markdown) or `"tool"` (native function calling).
