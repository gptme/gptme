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

## Conversation and Session Concepts

(conversation)=
### Conversation
The primary unit of persistence in gptme. A named history of messages exchanged with an LLM,
stored as a directory on disk. Each conversation has a unique ID (a random adjective-color-animal
name by default, or a name you supply with `--name`).

**Storage**: `~/.local/share/gptme/logs/<conversation-id>/conversation.jsonl`

**Commands**:
- Start: `gptme --name my-project "your prompt"` or just `gptme` (auto-names)
- Resume: `gptme --name my-project` (by name) or `gptme -r` (most recent)
- List: `gptme chats list`
- Search: `gptme chats search <query>`
- Rename: `/rename` in the TUI, or `gptme chats rename <id>`
- Delete: `/delete` in the TUI

**Disambiguation**: "conversation" is the persistent log on disk. A "run" or "session" (everyday use)
means launching gptme and working in a conversation — no special object, just a process attached to
a log. For analytics-tracking sessions, see [Analytics Session](#analytics-session).

(branch)=
### Branch
An alternative message thread stored alongside the main thread within the same conversation
directory. The default branch is named `"main"` and stored as `conversation.jsonl`.
Named branches are stored as `branches/<branch-name>.jsonl` in the same log directory.

Branches are mainly a **server-side concept**: the `gptme-server` API accepts a `branch` parameter
on step/tool calls, allowing multiple AI exploration paths in the same conversation simultaneously.
The TUI's `/fork` command creates a new top-level conversation rather than a branch within
the current one.

**Storage**: `…/<conversation-id>/branches/<branch-name>.jsonl`

(fork)=
### Fork
A new, independent conversation created from a copy of messages 0 through turn N of an
existing conversation. The original conversation is never modified. Both the TUI command
and the CLI produce a new conversation directory.

**Usage**:
```bash
# Fork at turn 3 (includes turns 0–2, the first 3 user+assistant exchanges)
gptme-util chats fork my-project --at-turn 3

# Fork with a custom name
gptme-util chats fork my-project --at-turn 3 --name my-project-v2

# Within a running conversation, fork at the current turn:
# /fork my-experiment
```

**Distinction from Branch**: A fork creates a separate conversation ID with its own directory;
a branch lives inside the original conversation's directory (server-side only).

### Log / LogManager
The conversation history and its management system. Stores all messages exchanged in a session.
`LogManager` handles file locking, branching, and workspace resolution. View reduction lives in
`gptme/util/reduce.py`; workspace symlink creation happens in `ChatConfig.save()`.

**Code reference**: `gptme/logmanager/manager.py`

### Workspace
The filesystem directory gptme operates in during a conversation. File operations, shell commands,
and checkpoints are scoped to this directory.

**Storage**: Symlinked at `…/<conversation-id>/workspace/` → the actual directory.

**Set via**: `gptme --workspace <dir>`

### Checkpoint
A clean git HEAD reference recorded in the workspace's git history. Use to restore the workspace
to a known good state before large or risky changes. Requires a committed working tree.

**Commands**: `/checkpoint create`, `/checkpoint list`, `/checkpoint diff <id>`, `/checkpoint restore <id>`

**Distinction from Snapshot**: Checkpoints require a committed tree; snapshots capture any state
including uncommitted changes.

### Snapshot
A workspace state capture recorded in a side-git shadow repository. Can capture committed
or uncommitted changes. Created automatically before and after each mutating tool call when
the `auto_snapshots` plugin is enabled.

**Commands**: `/snapshot create [label]`, `/snapshot list`, `/snapshot restore <sha>`, `/snapshot diff <sha>`

### Backtrack Marker
A named conversation position persisted to disk (in `conv-checkpoints.jsonl` alongside the log).
Rewinding with `/backtrack` truncates the conversation log to the saved index and creates a backup
branch — the log on disk is modified, but workspace files are not.

**Commands**: `/backtrack mark [label]`, `/backtrack list`, `/backtrack <label|N>`

**Distinction**: Unlike `/checkpoint` and `/snapshot`, backtracking does not touch workspace files.

(analytics-session)=
### Analytics Session
A completed run record stored by the `gptme-sessions` package. Tracks metadata about a finished
run: duration, cost, model, category, and quality grades. Cross-harness: records runs from
gptme, Claude Code, Codex, and other compatible harnesses.

**Storage**: `~/.local/share/gptme-sessions/sessions.jsonl` (configurable)

**Commands**: `gptme sessions query`, `gptme sessions show <id>`, `gptme sessions stats`

**Disambiguation**: Not the same as "starting a session" (everyday usage for launching gptme
in a conversation). The analytics session is only created *after* the run completes.

## Configuration

### Model
The LLM backend used for generation (e.g., `openai/gpt-4`, `anthropic/claude-3`).

### Tool Format
How tools are presented to the model: `"markdown"` (tool blocks in markdown) or `"tool"` (native function calling).
