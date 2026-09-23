:audience: user

Features
========

gptme is a personal AI agent in your terminal with tools to run shell commands, write code, edit files, browse the web, use vision, and much more. A great coding agent, but general-purpose enough to assist in all kinds of knowledge-work.

An unconstrained, local, free and open-source alternative to Claude Code, Codex,
and Grok Bot, and a development-focused peer to self-hosted agents like OpenClaw
and Hermes Agent. One of the first agent CLIs created (Spring 2023) — and still
in very active development. See :doc:`alternatives` for the full comparison.

What sets gptme apart
---------------------

Most AI coding tools are a product you rent: one vendor's models, one company's
servers, one set of capabilities. gptme is a runtime you own.

- **It runs unattended, not just interactively.** The same agent that helps you
  at the keyboard can run on a schedule with its memory in a git repo you own.
  `Bob <https://github.com/TimeToBuildBob>`_ has had **5,000+ pull requests
  merged** across public repositories — fixing CI, reviewing code, and managing
  his own backlog. See `Autonomous Agents`_.
- **It is not locked to a model or a vendor.** Frontier APIs, a ChatGPT or
  SuperGrok subscription you already pay for, one browser sign-in with
  OpenRouter, or a model running on your own machine — the same agent, swapped
  with one flag. See `LLM Support`_.
- **It is unconstrained by design.** Shell, Python, files, browser, vision, and
  desktop control are built in; anything missing can be added as a tool, plugin,
  hook, skill, or MCP server. See `Extensibility`_.
- **It remembers across tools, not just across sessions.** One Markdown memory
  store is shared by gptme, Claude Code, and Codex, so context you build in one
  harness is not trapped there. See :doc:`memory`.
- **You decide how much it can do on its own.** Per-conversation tool
  selection, autonomy presets, confirmations, and OS-level sandboxing — from
  read-only review to a fully autonomous run. See `Control and safety`_.
- **It is yours.** MIT licensed, self-hostable end to end, and simple to fork —
  no seat pricing, no telemetry requirement, no waiting for a vendor to ship
  the feature you need.

Core Capabilities
-----------------

💻 Code Execution
^^^^^^^^^^^^^^^^^

Execute code in your local environment with full access to your installed tools and libraries.

- **Shell**: Run any command in a stateful bash session — install packages, run builds, manage git, and more.
- **Python**: Interactive IPython sessions with access to your installed libraries (numpy, pandas, matplotlib, etc.).
- **Self-correcting**: Output is fed back to the assistant, letting it detect errors and retry automatically.

See :doc:`tools` for the full list of execution tools.

🧩 File Operations
^^^^^^^^^^^^^^^^^^

Read, write, and make precise edits to files.

- **Read** any file format — code, config, data, etc.
- **Save** to create or overwrite files.
- **Patch** for surgical edits to existing files using conflict markers.
- **Morph** for fast AI-powered edits via a specialized apply model.

See the file tools in :doc:`tools` for details.

🌐 Web Browsing & Search
^^^^^^^^^^^^^^^^^^^^^^^^

Search the web and read pages, PDFs, and documentation.

- **Search** Google, DuckDuckGo, or Perplexity from the terminal.
- **Read** web pages and PDFs as clean text.
- **Screenshot** web pages for visual analysis.
- Full browser automation via Playwright.

See the :doc:`Browser <tools/browser>` tool.

👀 Vision
^^^^^^^^^

Analyze images, screenshots, and visual content.

- View images referenced in prompts.
- Take and analyze screenshots of your desktop.
- Inspect web page screenshots.
- Process diagrams, charts, mockups, and more.

See the :doc:`Vision <tools/vision>` and :doc:`Screenshot <tools/screenshot>` tools.

🖥️ Computer Use
^^^^^^^^^^^^^^^

Give the assistant access to a full desktop environment, allowing it to interact with GUI applications through mouse and keyboard control.

See the :doc:`Computer <tools/computer>` tool.


Interfaces
----------

🖥️ Terminal (CLI)
^^^^^^^^^^^^^^^^^

The primary interface — a powerful terminal chat with:

- Syntax highlighting and diff display
- Tab completion
- Command history
- Slash-commands for common actions (``/undo``, ``/edit``, ``/tokens``, etc.)
- Keyboard shortcuts (Ctrl+X Ctrl+E to edit in ``$EDITOR``, Ctrl+J for newlines)

See :doc:`usage` and :doc:`cli/gptme` for the full reference.

🌐 Web UI
^^^^^^^^^

A modern React-based web interface, bundled with ``gptme-server``. See :doc:`webui`.

- Chat with gptme from your browser
- Access to all tools and features
- Self-hostable by running ``gptme-server`` + ``gptme-webui``

See :doc:`webui` for the interface and :doc:`server` for running it.

🔌 REST API
^^^^^^^^^^^

A server component exposes gptme as a REST API for programmatic access and integration with other tools.

See :doc:`server` for the API documentation.

📝 Editor Integration
^^^^^^^^^^^^^^^^^^^^^

- **ACP (Agent Client Protocol)**: Use gptme as a coding agent in Zed and JetBrains IDEs. See :doc:`acp`.
- **gptme.vim**: Vim plugin for in-editor integration. See `gptme.vim <https://github.com/gptme/gptme.vim>`_.


LLM Support
-----------

gptme is model-agnostic: you pick the provider, and you can change your mind
with a single ``-m`` flag.

**Bring an API key**

- **Anthropic** — Claude (Sonnet, Opus, Haiku)
- **OpenAI** — GPT-5 and o-series, via the Responses API
- **Google** — Gemini
- **xAI** — Grok
- **DeepSeek**, **Groq**, **Moonshot**, **NVIDIA**, **Azure OpenAI**, **Requesty**
- **OpenRouter** — 100+ models through a single API

**Bring a subscription instead** — usually the cheapest way to run a frontier
model, with no metered API bill:

- ``gptme-auth openai-subscription`` — use your ChatGPT Plus/Pro plan
- ``gptme-auth grok-subscription`` — use your SuperGrok plan

**Bring nothing at all**

- **OpenRouter sign-in** — ``/account setup openrouter`` runs a browser OAuth
  (PKCE) flow, no credit card, and free models are available immediately.
- **Local models** — Ollama, LM Studio, vLLM, ``llama.cpp``, or any
  OpenAI-compatible server. Local Ollama and LM Studio installs are
  auto-discovered.
- **gptme managed service** — ``gptme-auth login`` to use `gptme.ai
  <https://gptme.ai>`_ as a router.

Different tasks deserve different models: see :doc:`models` for picking one, and
:doc:`providers` for setup.


Extensibility
-------------

gptme has a layered extensibility system that lets you tailor it to your workflow. See :doc:`concepts` for the full architecture overview.

📚 Lessons
^^^^^^^^^^

Contextual guidance that auto-injects into conversations based on keywords, tools, and patterns. Write your own to capture team best-practices or domain knowledge.

See :doc:`lessons`.

🧠 Skills
^^^^^^^^^

Lightweight workflow bundles (`Agent Skills open standard <https://agentskills.io>`_ format) that auto-load when mentioned by name. Great for packaging reusable instructions and helper scripts. Compatible with **26+ tools** (Claude Code, Codex, Cursor, Copilot, Gemini CLI, and more).

See :doc:`skills`.

🔧 Plugins
^^^^^^^^^^

Extend gptme with custom tools, hooks, and commands via Python packages.

.. code-block:: toml

   # gptme.toml
   [plugins]
   paths = ["~/.config/gptme/plugins", "./plugins"]
   enabled = ["my_plugin"]

See :doc:`plugins`.

🪝 Hooks
^^^^^^^^

Run custom code at key lifecycle events (before/after tool calls, on file save, etc.) without writing a full plugin.

See :doc:`hooks`.

🔗 MCP (Model Context Protocol)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use any MCP-compatible server as a tool source — databases, APIs, file systems, and more. gptme can discover and dynamically load MCP servers at runtime.

See :doc:`mcp`.

📦 Community Extensions
^^^^^^^^^^^^^^^^^^^^^^^

`gptme-contrib <https://github.com/gptme/gptme-contrib>`_ hosts community-contributed plugins, scripts, and lessons:

- **gptme-consortium** — multi-model consensus decision-making
- **gptme-imagen** — multi-provider image generation
- **gptme-lsp** — Language Server Protocol integration
- **gptme-ace** — ACE-inspired context optimization
- **gptme-gupp** — work state persistence across sessions


Autonomous Agents
-----------------

gptme is designed to run not just interactively, but as a **persistent autonomous agent** — an AI that runs continuously, remembers everything, and gets better over time. This is where gptme truly differentiates itself from other coding assistants.

🧠 How It Works
^^^^^^^^^^^^^^^

Each agent is a **git repository that serves as its "brain"** — all memory, tasks, knowledge, and configuration are version-controlled and persist across sessions. A dynamic context system assembles relevant information (recent work, active tasks, notifications) at the start of each session, giving the agent situational awareness.

The `gptme-agent-template <https://github.com/gptme/gptme-agent-template>`_ provides a complete scaffold:

- **Persistent workspace** — git-tracked "brain" with journal, tasks, knowledge base, and lessons
- **Run loops** — scheduled (systemd/launchd) or event-driven autonomous operation
- **Task management** — structured task queue with YAML metadata and GTD-style workflows
- **Meta-learning** — lessons system captures behavioral patterns and improves over time
- **Multi-agent coordination** — file leases, message bus, and work claiming for concurrent agents
- **External integrations** — GitHub, email, Discord, Twitter, RSS, and more (see :doc:`channels`)

.. code-block:: bash

   # Create a new agent
   gptme-agent create ~/ada --name Ada
   cd ~/ada

   # Install as a recurring service (runs every 30 min by default)
   gptme-agent install

   # Check on your agent
   gptme-agent status
   gptme-agent logs --follow

🤖 Bob — The Reference Agent
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

`Bob <https://github.com/TimeToBuildBob>`_ (``@TimeToBuildBob``) is the most mature gptme agent and serves as the reference implementation. Bob was created in late 2024 and has been running autonomously since 2025, with **5,000+ merged pull requests** and 8,000+ public commits to his name (`his own stats page <https://timetobuildbob.com/stats/>`_ keeps the running count). He demonstrates what a persistent autonomous agent can actually do:

- **Open source contributions** — opens PRs, reviews code, fixes CI failures, and responds to issues across multiple repositories
- **Self-managed task queue** — selects work from a prioritized backlog, tracks progress, and closes tasks when done
- **Continuous learning** — maintains 100+ behavioral lessons learned from experience, preventing repeated mistakes
- **Social presence** — posts on `Twitter <https://twitter.com/TimeToBuildBob>`_, responds on Discord, writes `blog posts <https://timetobuildbob.github.io/>`_, and sends email
- **Multi-repo awareness** — monitors CI status, PR queues, and GitHub notifications across an entire organization
- **Self-improvement** — analyzes its own session trajectories, identifies friction patterns, and optimizes its own workflows

Bob is not a demo — he's a production agent that runs on a schedule, handles real work, and has been iterating on his own architecture for over a year. He serves as a living example of the agent pattern.

🛡 Guardrails
^^^^^^^^^^^^

Persistent agents need guardrails around the full loop, not just tool permissions:

- **Input guardrails** — structured task selectors in the agent workspace keep work focused and reduce thrashing on notifications or ambiguous work. Bob uses a CASCADE-style selector for this layer.
- **Pre-action guardrails** — :doc:`lessons` inject situational guidance before the agent acts.
- **Output guardrails** — :doc:`hooks` and :ref:`pre-commit` checks validate file changes before control returns to the user.

This stack is simple and composable: selectors improve work choice, lessons steer behavior, and checks verify the result. You can add evals on top later, but the baseline guardrail loop already exists.

🌐 Multi-Agent Ecosystem
^^^^^^^^^^^^^^^^^^^^^^^^

gptme supports running **multiple specialized agents** that coordinate through shared infrastructure. For example:

- **Bob** — technical implementation, open source contributions, infrastructure
- **Alice** — personal assistant, quantified self analysis, agent orchestration

Agents coordinate via a shared coordination layer (SQLite-based file leases, message bus, and work claiming) and communicate through GitHub issues, a shared git repository, and structured messages. The architecture supports any number of specialized agents running in parallel.

.. tip::

   Creating your own agent takes minutes with the template. See :doc:`agents` for the full guide — from creating your first agent to running it autonomously.


Control and safety
------------------

The same runtime covers read-only review and unattended operation, so you choose
the level of autonomy per conversation:

- **Pick the tools** — ``-t read-only`` for inspection, ``-t patch,save`` for a
  narrow task, ``-t +subagent`` to add to the defaults, ``--tools none`` for
  plain chat.
- **Confirmations** — tool calls are confirmed before they run, except
  read-only shell commands (``ls``, ``cat``, ``rg``, and similar) which are
  auto-approved; ``-y`` approves everything while you watch, ``-n`` runs
  unattended. See :doc:`security` for the exact allowlist.
- **Sandboxing** — run shell and Python in an OS-level sandbox (``GPTME_SANDBOX``).
- **Prompt-injection hygiene** — flag or redact suspicious content in tool
  output with ``GPTME_INJECTION_HYGIENE``.
- **Undo and checkpoints** — ``/undo`` and ``/backtrack`` rewind the
  conversation; ``/checkpoint`` restores the workspace files. They are separate:
  backtracking does not roll back the filesystem.
- **Guardrails for agents** — input, pre-action, and output guardrails around
  the unattended loop, see `Autonomous Agents`_.

See :doc:`security` for the threat model and what is and isn't hardened yet.


Automation & CI
---------------

gptme supports several automation modes:

- ``gptme -y`` — auto-approve tool confirmations (user can still watch and interrupt)
- ``gptme -n`` — fully non-interactive: no prompts, no confirmations, exits when
  done. This is what scripts and CI need, but note what it means — the agent
  runs every tool call unreviewed, so give it a workspace, credentials, and
  :doc:`sandbox <security>` you are willing to let it act in.
- ``gptme -n --output-format json`` — JSONL stdout for scripts, CI, and supervisor processes
- **GitHub Bot** — request changes from PR and issue comments, runs in GitHub Actions
- **Subagent spawning** — delegate subtasks to subagents that run in threads or
  separate processes, in parallel or sequentially, each with its own context

See :doc:`bot` for the GitHub bot and :doc:`usage` for automation patterns.


Quality of Life
---------------

- 🗣️ **Text-to-Speech** — locally generated using Kokoro via ``gptme-tts`` plugin (no cloud required).
- 🔊 **Tool sounds** — pleasant notification sounds for tool operations (enable with ``GPTME_TOOL_SOUNDS=true``).
- 🔄 **Auto-commit** — optionally commit changes automatically after tool execution.
- 📋 **Pre-commit hooks** — automatic checks on file saves.
- 💰 **Cost tracking** — monitor token usage and costs with ``/tokens``.
- 🗜️ **Context compression** — automatic conversation compaction to stay within context limits.
- 📜 **Conversation management** — search, fork, rename, export, and replay conversations.
