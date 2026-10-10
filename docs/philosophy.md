---
audience: user
---

# Philosophy

What gptme is for, the principles behind its design, and what it deliberately
is not. This page is a starting point for discussion
([#1294](https://github.com/gptme/gptme/discussions/1294)), not a contract —
if it disagrees with how gptme actually behaves, the behavior is the bug report.

## Mission

gptme is an open-source, model-agnostic AI agent that lives in your terminal and
runs on your machine. The same runtime serves two uses: an interactive coding
assistant you talk to, and a **persistent autonomous agent** that runs unattended
and keeps its memory in a git repository you own.

## Principles

### The terminal is home

The terminal composes, scripts, pipes, records and versions. gptme lives there by
choice. Other interfaces (web UI, desktop app, voice, chat channels) are the same
runtime projected into a different medium, not separate products.

### Local-first, you own the compute

By default gptme runs on your machine against your own API keys or local models.
Conversation logs and files are stored locally unless you choose a hosted setup,
but cloud models receive the conversations and context you supply, including
file contents read into the conversation. For data that must stay on your
machine, use a local model; see [security](security.rst).
A tool that can run offline against open-weight models remains yours regardless
of what happens to any one vendor or endpoint.

### Composable, Unix-style

gptme gives an LLM a small set of primitives (`shell`, `python`, file read/save/patch,
browsing, and others) and lets it use the rest of your environment through them,
the way grep composes with sed. New capability should arrive as a tool, a plugin,
an MCP server, a skill, or a script, not as another special case in the core.

### Interactive and autonomous are the same loop

There is no separate "agent API". A workflow built interactively can be automated
without rewriting it, and an unattended run can be opened and debugged
interactively. The same tools, the same conversation log.

### Model-agnostic

Anthropic, OpenAI, Gemini, OpenRouter, local models, any OpenAI-compatible
endpoint. The model is configuration, not an architectural assumption. Use
whichever fits the task on cost, latency, privacy and capability.

### Explicit and inspectable

Context is files, lessons, skills and memory entries you can read, edit and
version. What the agent did is in the conversation log. Convenience features
should never make the agent's behavior harder to audit.

### Agents that compound

An agent's identity, memory, lessons and tasks live in a git repository
(see [agents](agents.rst) and [memory](memory.rst)). Each session can leave the next
one better equipped: a lesson written today is matched automatically tomorrow.
Long-lived, improving agents are the design constraint behind most of gptme's
architecture.

## What gptme is not

- **Not just a chat client.** The goal is a capable runtime where the model can
  act, not only converse.
- **Not tied to a hosted service.** [gptme.ai](cloud.rst) is a managed convenience
  for people who don't want to run gptme themselves. The open-source tool is the
  product and runs anywhere.
- **Not an editor plugin.** IDE assistants optimize for inline completion; gptme
  optimizes for carrying out whole tasks, in the terminal or unattended.
  See [alternatives](alternatives.rst) for a fuller comparison.
- **Not a walled garden.** Open formats (Markdown, git, MCP), no proprietary
  protocols, no data routed through gptme-operated servers by default.
- **Not opaque.** Agents may run lights-out, with nobody watching, but they remain
  transparent to the person who owns them: everything is logged and inspectable,
  and workspace changes can be reviewed and reverted once committed to version
  control. Actions outside the repository need separate safeguards; see
  [autonomous-operation guardrails](agents/autonomous.rst).

## On autonomous operation

Discussion #1294 frames gptme as a driver for "dark factory" style headless
agents. That is a use gptme is designed to support: it is how gptme-based agents
such as [Bob](agents.rst) operate day to day, without a human in the loop for
each step. The principle behind it is oversight by inspection, not by constant
attendance: you set the goals and guardrails, the agent does the work, and the
record of what it did is always there to read.

## Where this is heading: guardian angels

The long-term direction is close to what Gwern calls a
[guardian angel](https://gwern.net/guardian-angel): a personal agent that works
for one person, knows their values and preferences, and **amplifies them rather
than replacing them**. A generic chatbot has no principal; it answers everyone the
same way and forgets every correction. A guardian angel is loyal to its owner,
learns from them over time, and keeps that learning when the underlying model is
swapped out.

gptme already has the substrate for this: an agent's identity, memory, lessons and
history live in a git repository the owner controls, so what the agent learns
outlives any single model or provider. What it does not do is learn at the
weight level: personalization today is context (lessons, memory, identity files)
rather than continuous fine-tuning on the owner's own data. Closing that gap, while keeping the agent local-first and
inspectable, is the open problem.

## Contributing with this in mind

When proposing a change, it helps to say which principle it serves. Changes that
strengthen composability, inspectability, or the autonomous-agent workflow are the
easiest to accept; new mechanisms that duplicate an existing one, or that tie
gptme to a single provider or service, are the hardest. See
[contributing](contributing.rst).
