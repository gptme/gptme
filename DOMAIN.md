# gptme Domain Guide

This is a compact map of gptme's core vocabulary and repository boundary. It is
for contributors and coding agents deciding where a change belongs. Follow the
linked code and documentation for the full contracts.

## Core vocabulary

### Tool

A capability the assistant can invoke to act outside ordinary text generation,
such as reading a file, running a command, or calling an API. At runtime a tool
is described by a [`ToolSpec`](gptme/tools/base.py): its name, instructions,
availability, invocation format, and execution handler. A tool can be known but
not currently available or runnable. Tool calls parsed from assistant messages
are represented separately as `ToolUse` objects.

See [Tools](docs/tools.rst) and the [glossary](docs/glossary.md#tool-concepts).

### Provider

An LLM service or transport selected by the prefix of a model identifier, such
as `anthropic` in `anthropic/claude-sonnet-4-6`. Providers own the API-specific
translation from gptme messages and tools to a model request and back. Built-in
provider identifiers and plugin metadata live under
[`gptme/llm/`](gptme/llm/); third-party providers can register through the
plugin system. A provider is not a model: one provider can expose many models.

See [Providers](docs/providers.rst) and
[`ProviderPlugin`](gptme/llm/models/types.py).

### Message

The atomic conversation record. [`Message`](gptme/message.py) has a role
(`system`, `user`, or `assistant`), textual content, optional file attachments,
timestamp, visibility/context flags, call ID, and metadata. Ordered messages
form a conversation log; a message is not itself a turn or a persisted
conversation.

See the [conversation terminology](docs/glossary.md#conversational-concepts) and
[`LogManager`](docs/glossary.md#log--logmanager).

### Context

The effective input prepared for one model generation. It starts from stored
messages and may include system prompts, tool definitions, configured files,
dynamic context, attached-file contents, hook output, and reductions needed to
fit the model's context window. Context is therefore a per-generation view, not
a second persistence store and not a synonym for the conversation log.

See [`prepare_messages()`](gptme/logmanager/manager.py),
[agent context generation](docs/agents.rst), and the
[context-window definition](docs/glossary.md#context-window).

### Config

The resolved settings that control a gptme run. [`Config`](gptme/config/core.py)
combines user configuration, project configuration from `gptme.toml`, and
per-chat state; CLI options and environment variables can override configured
values. The narrower dataclasses under [`gptme/config/`](gptme/config/) own the
individual layers. "Config" does not mean only `gptme.toml`.

See [Configuration](docs/config.rst).

## Repository boundary

**gptme owns the runtime engine:** the CLI, server and web UI, conversation and
message lifecycle, provider adapters, built-in tools, configuration loading,
hooks, and extension interfaces. Keep core small and put broadly useful runtime
contracts here.

Adjacent repositories have different responsibilities:

- **[gptme-contrib](https://github.com/gptme/gptme-contrib)** owns composable
  plugins, packages, skills, lessons, scripts, and specialized or experimental
  integrations built on the core extension interfaces. Start there when a
  capability is not needed by most gptme users.
- **[gptme-cloud](https://github.com/gptme/gptme-cloud)** owns the managed
  service: the gptme.ai product surface, authentication, billing, account and
  instance management, and deployment infrastructure. Cloud product policy and
  hosted-service orchestration do not belong in the runtime engine.

See [Core vs gptme-contrib](AGENTS.md#core-vs-gptme-contrib),
[Are we tiny?](docs/arewetiny.rst), and the [ecosystem map](README.md#-ecosystem).
