:audience: user

Timeline
========

A brief timeline of the project.

The idea is to later make this into a timeline similar to the one for `ActivityWatch <https://activitywatch.net/timeline/>`_, including releases, features, etc.

.. figure:: https://raw.githubusercontent.com/gptme/stats/master/charts/stars.svg
   :alt: Stargazers over time
   :target: https://github.com/gptme/stats

   GitHub stargazers over time

..
    This timeline tracks development across the entire gptme ecosystem, including:

    - `gptme <https://github.com/gptme/gptme>`_ (main repository)
    - `gptme-agent-template <https://github.com/gptme/gptme-agent-template>`_
    - `gptme-rag <https://github.com/gptme/gptme-rag>`_
    - `gptme.vim <https://github.com/gptme/gptme.vim>`_
    - `gptme-webui <https://github.com/gptme/gptme-webui>`_ (archived; merged into main gptme repo)

    For repositories with formal releases, we track significant version releases.
    For repositories without formal releases (like gptme.vim), we track initial
    releases and major feature additions based on commit history.

    This file can be automatically updated by gptme with the help of `gh release list` and `gh release view` commands.

    Source of truth and sync:

    - This file is the source of truth for the gptme project timeline.
      The "📢 News" list in README.md is a brief, manually synced mirror of it; update both together.
    - Per-release details live in docs/releases/*.md (generated from git history
      by scripts/build_changelog.py). docs/changelog.rst is the toctree; GitHub
      release notes are copied from those files.
    - Community and usage stats (stars, downloads, contributors) live in https://github.com/gptme/stats,
      which also renders the stargazers chart above.
    - Bob's personal month-by-month timeline, which covers his contributions rather than gptme releases, is separate:
      TimeToBuildBob/TimeToBuildBob.github.io _data/timeline.yml, rendered at
      https://timetobuildbob.github.io/timeline/ and into the TimeToBuildBob GitHub profile README.

Unreleased
----------

- ``gptme service init`` scaffold for headless agents (systemd, and launchd on macOS)
- Cross-harness memory: ``gptme.memory`` package and ``gptme-util memory`` CLI, compatible with Claude Code and Codex memory
- Skills invocable as slash commands (``/skill:<name>``)
- Auto-discovery of local Ollama and LM Studio providers
- Offline file format conversion tool
- Trust-on-first-use gate for project-level shell execution

2026
----

August

- v0.33.0 (2026-08-19)

  - Snapshot-anchored ``hashline_edit`` edit format with 3-way merge recovery
  - Sandboxed execution: Docker and Wasmtime WASI backends for the Python tool, sandbox mode for the shell tool
  - Non-interactive mode: fatal error envelope and exit code taxonomy
  - Autocompact ``keep_head`` to protect task context from compaction
  - New commands and modes: ``gptme explain``, ``gptme providers add``, read-only audit tool preset, interactive tutorial onboarding
  - Security hardening: bearer auth required on all server bind addresses, per-binary shell allowlist flags (GHSA-mfh4-cxj2-jc9p)
  - Native Kimi K3 support, SuperGrok subscription provider, OpenRouter OAuth in first-run setup

- `gptme-skills-cc <https://github.com/gptme/gptme-skills-cc>`_ created (2026-08-05): gptme agent skills packaged as a Claude Code plugin

July

- v0.32.1 (2026-07-17)

  - Auto-updater for the desktop app
  - Textual-based terminal UI (``gptme-tui``)
  - Prompt-injection screening (``--injection-hygiene``) for file reads, shell, and MCP output
  - Subagent fleet controls: shared token budget, ``max_concurrent`` cap, ``cancel_on_failure``, cooperative cancellation
  - Gear autonomy presets and brief response mode

- v0.32.0 (2026-07-11)

  - Agent Client Protocol (ACP) support with the ``gptme-acp`` package; gptme tools exposed as an MCP server (``gptme-mcp-server``); MCP prompts, resources, roots, and elicitation
  - Desktop app (Tauri) builds for Linux, macOS, and Windows, plus Android builds
  - Computer use: accessibility-tree actions on Linux and macOS, screen recording, audit log with risk labels
  - Subagents: parallel fan-out (``subagent_parallel``) and git worktree isolation
  - Evals: SWE-bench and Terminal-Bench modules, HTML leaderboard
  - Anthropic native web search, PDF support in browser tool, skills summary in system prompt, lesson keyword wildcards/regex, session cost summary on exit, Master Context Architecture for autocompact
  - New models: GPT-5.5, GPT-5.6 Luna/Terra/Sol, Claude Opus 4.7, DeepSeek V4

- `gptme-cc-plugin <https://github.com/gptme/gptme-cc-plugin>`_ created (2026-07-06): Claude Code skills for running gptme

May

- `gptme-plugin-registry <https://github.com/gptme/gptme-plugin-registry>`_ created (2026-05-12): central registry for plugin discovery

February

- Scheduled dev pre-releases begin (2026-02-27)

January

- gptme-agent-template v0.4 release (2026-01-23)

  - Autonomous agent run loops
  - Enhanced context generation
  - Bob reaches 1000+ autonomous sessions milestone

2025
----

December

- v0.31.0 (2025-12-15)

  - Background jobs for long-running shell commands
  - Form tool for structured user input
  - Cost tracking and token awareness hooks
  - Content-addressable file storage
  - Lessons caching and plugin auto-discovery
  - Cursor .mdc rules support

November

- v0.30.0 (2025-11-18)

  - Plugin system (tools, hooks, commands from plugins)
  - Context selector infrastructure
  - Subagent planner mode
  - Improved support for custom OpenAI-compatible providers

October

- v0.29.0 (2025-10-21)

  - Lessons system for contextual guidance (auto-included based on keywords/tools)
  - MCP discovery and dynamic loading
  - Token and time awareness hooks
  - Shellcheck validation for shell commands
  - Bob begins autonomous runs with GitHub monitoring

August

- v0.28.0 (2025-08-13)

  - MCP (Model Context Protocol) support
  - Morph tool for fast AI-powered edits
  - Auto-commit feature
  - Redesigned server API (v2)
  - ChatConfig for per-conversation settings

March

- v0.27.0 (2025-03-11)

  - Pre-commit integration for automatic code quality checks
  - macOS support for computer use tool
  - Claude 3.7 Sonnet and DeepSeek R1 support
  - Improved TTS with Kokoro 1.0
  - Context tree for including repository structure in prompts
  - Enhanced RAG with LLM post-processing

February

- Added image support to gptme-webui (2025-02-07)

January

- Major UI improvements to gptme-webui (2025-01-28)
- v0.26.0 (2025-01-14)

  - Added support for loading tools from external modules (custom tools)
  - Added experimental local TTS support using Kokoro

- gptme-contrib repository created (2025-01-10)

  - Initial tools: Twitter and Perplexity CLI integrations
  - Later expanded with Discord bot, Pushover notifications, and enhanced Twitter automation

2024
----

December

- v0.25.0 (2024-12-20)

  - New prompt_toolkit-based interface with better completion and highlighting
  - Support for OpenAI/Anthropic tools APIs
  - Improved cost & performance through better prompt caching
  - Better path handling and workspace context
  - Added heredoc support
- gptme-agent-template v0.3 release (2024-12-20)
- gptme-rag v0.5.1 release (2024-12-13)

November

- gptme.vim initial release (2024-11-29)
- v0.24.0 (2024-11-22)
- gptme-rag v0.3.0 release (2024-11-22)
- gptme-agent-template initial release v0.1 (2024-11-21)
- `Bob <https://github.com/TimeToBuildBob>`_ created (2024-11-14) - first autonomous agent built on gptme
- gptme-rag initial release v0.1.0 (2024-11-15)
- v0.23.0 (2024-11-14)
- gptme-webui initial release (2024-11-03)
- v0.22.0 (2024-11-01)

October

- v0.21.0 (2024-10-25)
- v0.20.0 (2024-10-10)

  - Updated web UI with sidebar
  - Improved performance with faster imports
  - Enhanced error handling for tools

- `First viral tweet <https://x.com/rohanpaul_ai/status/1841999030999470326>`_ (2024-10-04)
- v0.19.0 (2024-10-02)

September

- v0.18.0 (2024-09-26)
- v0.17.0 (2024-09-19)
- v0.16.0 (2024-09-16)
- v0.15.0 (2024-09-06)

  - Added screenshot_url function to browser tool
  - Added GitHub bot features for non-change questions/answers
  - Added special prompting for non-interactive mode

August

- v0.14.0 (2024-08-21)
- v0.13.0 (2024-08-09)

  - Added Anthropic Claude support
  - Added tmux terminal tool
  - Improved shell tool with better bash syntax support
  - Major tools refactoring

- v0.12.0 (2024-08-06)

  - Improved browsing with assistant-driven navigation
  - Added subagent tool (early version)
  - Tools refactoring

- `Show HN <https://news.ycombinator.com/item?id=41204256>`__

2023
----

November

- v0.11.0 (2023-11-29)

  - Added support for paths/URLs in prompts
  - Mirror working directory in shell and Python tools
  - Started evaluation suite

- v0.10.0 (2023-11-03)

  - Improved file handling in prompts
  - Added GitHub bot documentation

October

- v0.9.0 (2023-10-27)

  - Added automatic naming of conversations
  - Added patch tool
  - Initial documentation

- v0.8.0 (2023-10-16)

  - Added web UI for conversations
  - Added rename and fork commands
  - Improved web UI responsiveness

- v0.7.0 (2023-10-10)
- v0.6.0 (2023-10-10)
- v0.5.0 (2023-10-02)

  - Added browser tool (early version)

September

- v0.4.0 (2023-09-10)
- v0.3.0 (2023-09-06)

  - Added configuration system
  - Improved context awareness
  - Made OpenAI model configurable

- `Reddit announcement <https://www.reddit.com/r/LocalLLaMA/comments/16atlia/gptme_a_fancy_cli_to_interact_with_llms_gpt_or/>`_ (2023-09-05)
- `Twitter announcement <https://x.com/ErikBjare/status/1699097896451289115>`_ (2023-09-05)
- `Show HN <https://news.ycombinator.com/item?id=37394845>`__ (2023-09-05)
- v0.2.1 (2023-09-05)

  - Initial release

August

March

- `Initial commit <https://github.com/gptme/gptme/commit/d00e9aae68cbd6b89bbc474ed7721d08798f96dc>`_
