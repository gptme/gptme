Features
========

gptme is a personal AI agent in your terminal with tools to run shell commands, write code, edit files, browse the web, use vision, and much more. A great coding agent, but general-purpose enough to assist in all kinds of knowledge-work — including running as a **persistent autonomous agent** that operates continuously, learns from experience, and manages its own tasks.

An unconstrained local free and open-source alternative to Claude Code, Codex, Cursor Agents, etc. One of the first agent CLIs created (Spring 2023) — and still in very active development.

.. contents:: Table of Contents
   :depth: 2
   :local:
   :backlinks: none

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

See the :ref:`Browser <tools:Browser>` tool.

👀 Vision
^^^^^^^^^

Analyze images, screenshots, and visual content.

- View images referenced in prompts.
- Take and analyze screenshots of your desktop.
- Inspect web page screenshots.
- Process diagrams, charts, mockups, and more.

See the :ref:`Vision <tools:Vision>` and :ref:`Screenshot <tools:Screenshot>` tools.

🖥️ Computer Use
^^^^^^^^^^^^^^^

Give the assistant access to a full desktop environment, allowing it to interact with GUI applications through mouse and keyboard control.

See :doc:`tools` for the Computer tool documentation and the `computer use tracking issue <https://github.com/gptme/gptme/issues/216>`_.


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

See :doc:`usage` and :doc:`cli` for the full reference.

🌐 Web UI
^^^^^^^^^

A modern React-based web interface available at `chat.gptme.org <https://chat.gptme.org>`_.

- Chat with gptme from your browser
- Access to all tools and features
- Self-hostable by running ``gptme-server`` + ``gptme-webui``

See :doc:`server` for setup instructions.

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

gptme works with a wide range of LLM providers and models:

- **Anthropic** — Claude (Sonnet, Opus, Haiku)
- **OpenAI** — GPT-4o, GPT-4, o1, o3
- **Google** — Gemini
- **xAI** — Grok
- **DeepSeek** — DeepSeek R1 and others
- **OpenRouter** — Access 100+ models through a single API
- **Local models** — Run models locally via ``llama.cpp`` (no API key required)

See :doc:`providers` for setup instructions and model configuration.


Extensibility
-------------

gptme has a layered extensibility system that lets you tailor it to your workflow. See :doc:`concepts` for the full architecture overview.

📚 Lessons
^^^^^^^^^^

Contextual guidance that auto-injects into conversations based on keywords, tools, and patterns. Write your own to capture team best-practices or domain knowledge.

See :doc:`lessons`.

🧠 Skills
^^^^^^^^^

Lightweight workflow bundles (Anthropic format) that auto-load when mentioned by name. Great for packaging reusable instructions and helper scripts.

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
- **External integrations** — GitHub, email, Discord, Twitter, RSS, and more

.. code-block:: bash

   # Create a new agent
   gptme-agent create ~/my-agent --name MyAgent

   # Install as a recurring service (runs every 30 min by default)
   gptme-agent install

   # Check on your agent
   gptme-agent status
   gptme-agent logs --follow

🤖 Bob — The Reference Agent
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

`Bob <https://github.com/TimeToBuildBob>`_ (``@TimeToBuildBob``) is the most mature gptme agent and serves as the reference implementation. Bob has been running autonomously since 2024, completing **1700+ sessions** across hundreds of days. He demonstrates what a persistent autonomous agent can actually do:

- **Open source contributions** — opens PRs, reviews code, fixes CI failures, and responds to issues across multiple repositories
- **Self-managed task queue** — selects work from a prioritized backlog, tracks progress, and closes tasks when done
- **Continuous learning** — maintains 100+ behavioral lessons learned from experience, preventing repeated mistakes
- **Social presence** — posts on `Twitter <https://twitter.com/TimeToBuildBob>`_, responds on Discord, writes `blog posts <https://timetobuildbob.github.io/>`_, and sends email
- **Multi-repo awareness** — monitors CI status, PR queues, and GitHub notifications across an entire organization
- **Self-improvement** — analyzes its own session trajectories, identifies friction patterns, and optimizes its own workflows

Bob is not a demo — he's a production agent that runs on a schedule, handles real work, and has been iterating on his own architecture for over a year. He serves as a living example of the agent pattern.

🌐 Multi-Agent Ecosystem
^^^^^^^^^^^^^^^^^^^^^^^^

gptme supports running **multiple specialized agents** that coordinate through shared infrastructure. For example:

- **Bob** — technical implementation, open source contributions, infrastructure
- **Alice** — personal assistant, quantified self analysis, agent orchestration

Agents coordinate via a shared coordination layer (SQLite-based file leases, message bus, and work claiming) and communicate through GitHub issues, a shared git repository, and structured messages. The architecture supports any number of specialized agents running in parallel.

.. tip::

   Creating your own agent takes minutes with the template. See :doc:`agents` for the full guide — from creating your first agent to running it autonomously.


Automation & CI
---------------

gptme supports several automation modes:

- ``gptme -y`` — auto-approve tool confirmations (user can still watch and interrupt)
- ``gptme -n`` — fully non-interactive/autonomous mode (safe for scripts and CI)
- **GitHub Bot** — request changes from PR and issue comments, runs in GitHub Actions
- **Subagent spawning** — delegate subtasks to parallel agent instances via tmux

See :doc:`bot` for the GitHub bot and :doc:`usage` for automation patterns.


Quality of Life
---------------

- 🗣️ **Text-to-Speech** — locally generated using Kokoro (no cloud required). See the :ref:`TTS <tools:TTS>` tool.
- 🔊 **Tool sounds** — pleasant notification sounds for tool operations (enable with ``GPTME_TOOL_SOUNDS=true``).
- 🔄 **Auto-commit** — optionally commit changes automatically after tool execution.
- 📋 **Pre-commit hooks** — automatic checks on file saves.
- 💰 **Cost tracking** — monitor token usage and costs with ``/tokens``.
- 🗜️ **Context compression** — automatic conversation compaction to stay within context limits.
- 📜 **Conversation management** — search, fork, rename, export, and replay conversations.
