:audience: user

Alternatives
============

.. meta::
   :description: Compare gptme with Claude Code, Codex, Cursor, OpenClaw, Hermes Agent, Grok Bot, and other AI coding and personal agents. Open source, self-hosted, model-agnostic, terminal-native.
   :keywords: Claude Code alternative, OpenClaw alternative, Hermes Agent alternative, Grok Bot alternative, open source coding agent, self-hosted AI agent, persistent AI agent, autonomous agent comparison, gptme vs Claude Code, gptme vs OpenClaw

Looking for an open source Claude Code alternative, or a self-hosted personal
agent you can leave running? gptme is a model-agnostic, extensible AI assistant
for the terminal that does both: an interactive coding agent, and a
**persistent autonomous agent** that runs unattended and keeps its memory —
in a git repository you own — between runs.

This page compares gptme against the leading AI coding tools and personal
agents, to help you pick the right one for your workflow.


What Makes gptme Different
---------------------------

A persistent agent that grows over time, learns you over time, and becomes
better at helping you over time — building workflows autonomously, taking over
real work, and monitoring the situation while you do something else. That is
what gptme has been built toward since 2024, when most AI coding tools were
still autocomplete with a chat box.

The rest of the field has since arrived at the same idea: OpenClaw and Nous
Research's Hermes Agent ship persistent agents that write their own skills, and
SpaceXAI's Grok Bot gives each bot its own machine, memory, and routines. gptme's answer to that cohort is the
substrate — the agent's brain is a git repository you own:

- **Persistent autonomous agents**: gptme powers agents that run thousands of sessions autonomously — writing code, submitting PRs, monitoring CI, and learning from their own mistakes. :doc:`Bob <agents>` has been doing it since 2025, with 5,000+ merged pull requests to show for it.
- **Cross-harness memory**: ``gptme-util memory`` is one local Markdown memory store, in Claude Code's format, shared between gptme, Claude Code, and Codex sessions. Your context is not trapped in one vendor's tool. See :doc:`memory`.
- **Git as the brain**: the :doc:`agent template <agents>` puts agent identity, memory, lessons, and workspace in a git repo by default, so everything an agent knows is versioned, auditable, and forkable.
- **Model-agnostic**: Works with OpenAI, Anthropic, local models, or any OpenAI-compatible API. You're never locked in.
- **Self-written lessons**: Autonomous agents record :doc:`lessons <lessons>` from their own runs and edit their own configuration, creating a self-improving feedback loop.
- **Extensible tool system**: Shell, Python, file editing, web browsing, vision, MCP — and you can add your own tools.
- **Open source**: MIT licensed, fully inspectable, forkable. Your agent, your rules.

The git-as-agent-brain approach has also been explored in Oxford's `Git Context Controller paper <https://arxiv.org/html/2508.00031v1>`_, which stores agent context and memory in git repositories and reports 48.0% on SWE-Bench-Lite — the best result among the 26 systems it compares against.


Feature Comparison
------------------

.. |check| unicode:: U+2705
.. |cross| unicode:: U+274C
.. |partial| unicode:: U+1F7E1

gptme is compared against two different groups of tools, because it belongs to
both. The first is the coding agents people usually arrive from. The second is
the persistent personal agents that gptme has been built toward since 2024 —
and which the rest of the field has been moving toward since early 2026.

Coding agents
^^^^^^^^^^^^^

Interactive tools you drive from a terminal or an editor.

.. list-table:: Coding agents
   :widths: 22 9 9 9 9 9 9 9
   :header-rows: 1

   * - Feature
     - gptme
     - Claude Code
     - Codex
     - Cursor
     - Cline
     - Aider
     - OpenHands
   * - Open source
     - |check|
     - |cross|
     - |check|
     - |cross|
     - |check|
     - |check|
     - |check|
   * - Model-agnostic
     - |check|
     - |cross|
     - |partial|
     - |partial|
     - |check|
     - |check|
     - |check|
   * - Terminal-native
     - |check|
     - |check|
     - |check|
     - |partial|
     - |check|
     - |check|
     - |partial|
   * - Self-hosted
     - |check|
     - |cross|
     - |check|
     - |cross|
     - |check|
     - |check|
     - |check|
   * - Plugin/tool system
     - |check|
     - MCP
     - MCP
     - MCP
     - MCP
     - |check|
     - |check|
   * - Web UI
     - |check|
     - |check|
     - |check|
     - |check|
     - N/A
     - |partial|
     - |check|
   * - Autonomous mode
     - |check|
     - |partial|
     - |partial|
     - |partial|
     - |partial|
     - |cross|
     - |partial|
   * - Price
     - Free
     - $20/mo+
     - Free
     - $20/mo
     - Free
     - Free
     - Free

Persistent personal agents
^^^^^^^^^^^^^^^^^^^^^^^^^^

Agents that keep running when you are not watching, keep memory between runs,
and take on work rather than answer questions.

.. list-table:: Persistent personal agents
   :widths: 22 9 9 9 9 9 9
   :header-rows: 1

   * - Feature
     - gptme
     - OpenClaw
     - Hermes Agent
     - Grok Bot
     - CMA
     - Devin
   * - Open source
     - |check|
     - |check|
     - |check|
     - |cross|
     - |cross|
     - |cross|
   * - Self-hosted
     - |check|
     - |check|
     - |check|
     - |cross|
     - |partial|
     - |cross|
   * - Model-agnostic
     - |check|
     - |check|
     - |check|
     - |cross|
     - |cross|
     - |partial|
   * - Terminal-native
     - |check|
     - |partial|
     - |check|
     - |cross|
     - |cross|
     - |cross|
   * - Autonomous mode
     - |check|
     - |check|
     - |check|
     - |check|
     - |check|
     - |check|
   * - Git-based agent workspace
     - |check|
     - |cross|
     - |cross|
     - |cross|
     - |cross|
     - |cross|
   * - Writes its own skills
     - |check|
     - |check|
     - |check|
     - |partial|
     - |cross|
     - |cross|
   * - Cross-harness memory
     - |check|
     - |cross|
     - |cross|
     - |cross|
     - |cross|
     - |cross|
   * - Price
     - Free
     - Free
     - Free
     - $20/mo
     - pay-per-token
     - $200/mo
   * - Runtime fee
     - $0
     - $0
     - $0
     - bundled
     - $0.08/hr (~$58/mo)
     - bundled

Rows that need a definition:

- **Autonomous mode** — runs unattended on a schedule or trigger, not just as an
  interactive session you drive turn by turn.
- **Git-based agent workspace** — the agent's identity, tasks, journal, and
  knowledge live in a git repository you own, versioned, diffable, and forkable.
  This is how gptme's :doc:`agent template <agents>` is set up by default, not a
  hard requirement of the runtime.
- **Writes its own skills** — the agent turns completed work into reusable
  instructions for its future self. In gptme these are :doc:`lessons` and
  :doc:`skills`, written during autonomous runs and committed to the workspace.
- **Cross-harness memory** — a memory store other agent harnesses can read and
  write. Several tools ship a memory feature of their own (Claude Code's memory
  files, Cursor's Memories), but each is locked to that tool; gptme's
  :doc:`memory` uses Claude Code's format and layered roots, so gptme, Claude
  Code, and Codex sessions share the same entries.

.. note::

   Star counts and pricing move fast in this space; the numbers on this page were
   last checked 2026-09-23. Ownership moves fast too: Cursor has been a SpaceXAI
   (formerly xAI) product since August 2026, and Grok Bot is a joint product of
   the two.

Overview
--------

.. list-table:: Overview
   :widths: 18 9 18 9 13 9 12
   :header-rows: 1

   * -
     - Type
     - Focus
     - Hosting
     - Price
     - Funding
     - Open Source
   * - gptme
     - CLI
     - General purpose
     - Local
     - Free
     - Bootstrap
     - |check|
   * - Claude Code
     - CLI
     - Coding
     - Cloud
     - $20/mo+
     - VC
     - |cross|
   * - Claude Managed Agents
     - API
     - Autonomous agents
     - Cloud
     - $0.08/hr + tokens
     - VC
     - |cross|
   * - Aider
     - CLI
     - Coding
     - Local
     - Free
     - Bootstrap
     - |check|
   * - Cursor
     - IDE fork
     - Coding
     - Desktop
     - $20/mo
     - SpaceXAI
     - |cross|
   * - OpenHands
     - CLI/Web
     - General purpose
     - Both
     - Free
     - VC
     - |check|
   * - Codex
     - CLI
     - Coding
     - Local
     - Free
     - VC
     - |check|
   * - Cline
     - IDE / CLI
     - Coding
     - Local
     - Free
     - VC
     - |check|
   * - OpenClaw
     - Gateway
     - Personal assistant
     - Local
     - Free
     - Foundation
     - |check|
   * - Hermes Agent
     - Daemon
     - Personal assistant
     - Local
     - Free
     - Nous Research
     - |check|
   * - Grok Bot
     - Cloud agents
     - Autonomous agents
     - Cloud
     - $20/mo
     - SpaceXAI
     - |cross|
   * - Lovable.dev
     - Web app
     - Frontend
     - SaaS
     - Credits
     - VC
     - |cross|
   * - Devin
     - Web app
     - Coding
     - SaaS
     - $200/mo
     - VC
     - |cross|
   * - Moatless Tools
     - CLI
     - Coding
     - Local
     - Free
     - Bootstrap
     - |check|


Projects
--------

gptme
^^^^^

gptme is a personal AI assistant that runs in your terminal, designed for coding, automation, and knowledge work. It supports persistent autonomous operation, where agents run continuously with git-based memory.

Key features:

- Runs in the terminal, with optional web UI
- Executes shell commands, Python code, and more
- Reads, writes, and patches files
- Web browsing and vision support
- Self-correcting behavior
- Support for any LLM provider (OpenAI, Anthropic, local models)
- Extensible tool and plugin system with MCP support
- Persistent autonomous mode with self-improving feedback loop
- Highly customizable — simple to fork and modify

First commit: March 24, 2023.

Claude Code
^^^^^^^^^^^

`Claude Code <https://docs.anthropic.com/en/docs/claude-code/overview>`_ is Anthropic's agentic coding tool for the terminal. It is one of the most popular AI coding agents, with tight integration into Claude's capabilities.

Key features:

- Terminal-native with strong codebase understanding
- MCP support for extensibility
- CLAUDE.md project-level configuration
- Background agents and remote triggers
- Tight integration with Claude models

Differences to gptme:

- **Not open source** — cannot be inspected, forked, or self-hosted
- **Claude-only** — locked to Anthropic's models and pricing
- **Scheduled runs, not a persistent agent** — Routines (research preview) run
  Claude Code unattended on a schedule, an API call, or a GitHub event, but each
  run clones the repository fresh and starts a new session. There is no
  workspace that carries tasks, journal, and lessons from one run to the next,
  which is what a gptme agent accumulates
- gptme's autonomous agents have been validated over thousands of production sessions

Released February 24, 2025.

Aider
^^^^^

`Aider <https://aider.chat/>`_ is AI pair programming in your terminal, with excellent git integration and strong SWE-Bench performance.

Key features:

- Deep git integration with automatic commits
- Code editing with search/replace blocks
- Repository map for context
- Scores highly on SWE-Bench
- Support for many LLM providers

Differences to gptme:

- Aider is more git-commit-focused; gptme is more general-purpose
- gptme has a wider array of tools (shell, Python, browser, vision)
- gptme supports persistent autonomous operation; Aider is interactive-focused
- Aider's repository last saw a push in May 2026 (checked 2026-09-23)

First commit: April 4, 2023.

Cursor
^^^^^^

`Cursor <https://cursor.sh/>`_ is an AI-native IDE (VS Code fork) with excellent tab completion and inline editing.

Key features:

- AI-native IDE experience
- Git checkpointing
- Great tab completion (from `acquiring Supermaven <https://coplay.dev/blog/a-brief-history-of-cursors-tab-completion>`_)
- MCP support for extensibility

Differences to gptme:

- Cursor is an IDE; gptme is terminal-native
- gptme is open source and model-agnostic
- gptme is extensible with custom tools, more general-purpose

OpenHands
^^^^^^^^^

`OpenHands <https://github.com/All-Hands-AI/OpenHands>`_ (formerly OpenDevin) is a leading open-source platform for software development agents, with strong benchmark performance.

Key features:

- Strong performance on SWE-bench
- Can do anything a human developer can: write code, run commands, browse web
- Support for multiple LLM providers
- Both CLI and web interface
- Docker-based sandboxed execution
- Large community

Differences to gptme:

- OpenHands uses Docker-based sandboxing; gptme runs directly on the host
- OpenHands has a richer web UI
- gptme supports persistent autonomous operation with git-based memory
- gptme is simpler to set up and customize

First commit: March 13, 2024.

Codex
^^^^^

`Codex <https://github.com/openai/codex>`_ is OpenAI's open-source coding agent for the terminal. It was OpenAI's response to Claude Code.

Key features:

- Open source (Apache 2.0)
- Terminal-native
- Sandboxed execution
- Multimodal support

Differences to gptme:

- Codex is OpenAI-only; gptme is model-agnostic
- gptme has more tools and is more general-purpose
- gptme supports persistent autonomous operation

Released April 16th, 2025. (Not to be confused with OpenAI's earlier Codex model.)

Cline
^^^^^

`Cline <https://cline.bot/>`_ is an open-source coding agent that runs as an IDE extension, a CLI, or an SDK. Similar to Cursor's agent mode, but not a full VS Code fork.

It also had a fork called `Roo Code <https://github.com/RooCodeInc/Roo-Code>`_ (prev Roo Cline), archived in 2026.

Key features:

- Runs as a VS Code extension, a CLI, or an SDK
- MCP support for tool extensibility
- Open source

Differences to gptme:

- Cline runs in the IDE, terminal, and desktop, with a strong VS Code focus; gptme is terminal-first and built around an agent workspace
- gptme is model-agnostic and more general-purpose
- gptme supports persistent autonomous operation

Devin
^^^^^

`Devin <https://devin.ai/>`_ is the first widely-known "AI software engineer" — a fully autonomous coding agent that works in a sandboxed cloud environment.

Key features:

- Autonomous software engineering in a cloud sandbox
- Full development environment (editor, browser, terminal)
- Can plan, implement, test, and deploy independently
- Web-based interface with session replay

Differences to gptme:

- Devin is a cloud SaaS (from $20/mo, $200/mo for its top individual tier); gptme is free and self-hosted
- Devin is closed source; gptme is open source
- gptme runs locally on your machine with direct access to your environment
- gptme is model-agnostic; Devin uses proprietary models

OpenClaw
^^^^^^^^

`OpenClaw <https://github.com/openclaw/openclaw>`_ is an open-source, self-hosted personal AI assistant that connects to 25+ messaging channels (WhatsApp, Telegram, Slack, Discord, Signal, and more).

Key features:

- Multi-channel messaging gateway (25+ platforms)
- Self-hosted, privacy-first architecture
- Large skill marketplace (ClawHub, 5,400+ community skills)
- Voice support with wake words
- Plugin SDK for custom integrations

Differences to gptme:

- **Different front door, same premise**: OpenClaw is reached through chat channels, gptme through the terminal — but both are self-hosted persistent agents that write their own skills
- OpenClaw excels at messaging orchestration across platforms, and has a far larger skill ecosystem
- gptme excels at code generation, shell execution, and development workflows, and is the more natural fit where the work is a repository
- gptme's agent memory is a git repository you can diff, review, and fork; OpenClaw keeps its own memory store
- Both are open source, self-hosted, and model-agnostic

Hermes Agent
^^^^^^^^^^^^

`Hermes Agent <https://github.com/nousresearch/hermes-agent>`_ is Nous Research's
open-source personal agent, released February 2026. It lives on your own server,
keeps long-term memory, and writes reusable skills from completed work — the
closest analogue to the gptme agent pattern that exists.

Key features:

- Self-hosted, with persistent memory and a full terminal UI as a primary
  entry point
- Writes reusable skills after completing tasks, so capability compounds
- Works with any LLM provider, including local models via Ollama
- Seven terminal backends (local, Docker, SSH, Modal, and more), plus 20+
  messaging platforms through its gateway
- MIT licensed, single-command install

Differences to gptme:

- **The same thesis, a different substrate**: Hermes keeps memory in its own
  store; gptme keeps it in a git repository you own, so every change an agent
  makes to its own brain is a reviewable diff
- both are terminal-native; gptme is development-focused, while Hermes reaches
  further into messaging platforms and hosted sandboxes
- gptme's :doc:`memory` is shared with other harnesses (Claude Code, Codex)
  rather than being specific to gptme
- gptme works in both MCP directions and speaks :doc:`ACP <acp>` for editors
- Hermes has vastly more adoption; gptme has a longer autonomous track record
  through :doc:`Bob <agents>`

Grok Bot
^^^^^^^^

`Grok Bot <https://x.ai/>`_ is SpaceXAI and Cursor's "AI teammates" product,
launched in beta August 2026. Each bot gets its own persistent cloud computer,
memory, and routines, signs into your accounts, and works while you are away.

Key features:

- Named, persistent agents with their own cloud machine
- Memory, routines, and preference learning per bot
- Bots can coordinate with each other
- Included with SuperGrok and Cursor plans

Differences to gptme:

- **Someone else's computer**: Grok Bot runs on SpaceXAI infrastructure with your
  credentials on it; gptme runs on hardware you control
- Closed source and model-locked; gptme is MIT licensed and model-agnostic
- gptme's agent memory and configuration are files you can read, edit, and revert
- Grok Bot is the clearest sign that the persistent-agent pattern gptme has been
  building since 2024 is now mainstream

Moatless Tools
^^^^^^^^^^^^^^

`Moatless Tools <https://github.com/aorwall/moatless-tools>`_ is an AI coding agent optimized for `SWE-Bench <https://www.swebench.com/>`_ performance.

Key features:

- Various specialized tools for different tasks
- Focus on specific development workflows
- Scores highly on SWE-Bench

Lovable.dev
^^^^^^^^^^^

`lovable.dev <https://lovable.dev>`_ (previously GPT Engineer) lets you build webapps fast using natural language.

Key features:

- Builds frontends with ease, just by prompting
- LLM-powered no-code editor for frontends
- Git/GitHub integration
- Supabase integration for backend support

Differences to gptme:

- Lovable is a no-code web app builder; gptme is a terminal coding agent
- gptme is much more general-purpose
- Lovable is better at building polished frontends quickly

Disclaimer: gptme author Erik was an early hire at Lovable.


Claude Managed Agents
^^^^^^^^^^^^^^^^^^^^^

`Claude Managed Agents <https://platform.claude.com/docs/en/managed-agents/overview>`_ is Anthropic's hosted platform for running autonomous agents with sandboxed execution, built-in tools, and state management. Released April 8, 2026.

Key features:

- Cloud-hosted sandbox execution (no local setup required)
- Built-in tool suite (web search, code execution, file management)
- State management across tool calls within a session
- REST API for programmatic control

Differences to gptme:

- **Model lock-in**: Claude Managed Agents only runs Claude; gptme works with any provider
- **Runtime cost**: $0.08/hr for 24/7 agents (~$58/mo per agent) on top of token costs; gptme has no runtime fee
- **No self-hosting**: Cloud-only platform; gptme runs on your own machine
- **Memory is theirs, not yours**: CMA has cross-session memory, but it lives in Anthropic's control plane; a gptme agent's memory is files in a git repository you own, that you can read, diff, and fork

.. note::

   CMA launching validates the autonomous agent category. If Anthropic thinks managed
   agents are worth building, the open-source, model-agnostic alternative matters more.


Other Claude Products
^^^^^^^^^^^^^^^^^^^^^

Anthropic offers several AI products beyond Claude Code and Claude Managed Agents:

- **Claude Projects**: Upload files and chat with them in a project context. Released Jun 25, 2024.
- **Claude Artifacts**: Preview HTML and React components inline — like a mini Lovable.dev. Released Aug 27, 2024.
- **Claude Desktop**: Desktop client with MCP support for extensibility. Released October 31, 2024.


Other OpenAI Products
^^^^^^^^^^^^^^^^^^^^^

- **ChatGPT Code Interpreter**: One of the early inspirations for gptme. Gives ChatGPT access to a Python sandbox. Released July 6, 2023.
- **ChatGPT Canvas**: OpenAI's response to Claude Artifacts. Released October 3, 2024.


Open Interpreter
^^^^^^^^^^^^^^^^

`Open Interpreter <https://github.com/OpenInterpreter/open-interpreter>`_ is another open-source terminal AI assistant, similar in spirit to gptme.

Key features:

- Runs code locally in your terminal
- General-purpose assistant capabilities
- Support for multiple LLM providers

Differences to gptme:

- gptme has a more comprehensive tool system
- gptme supports persistent autonomous operation
- Both are open source and terminal-native
