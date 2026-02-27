<p align="center">
  <img src="https://gptme.org/media/logo.png" width=150 />
</p>

<h1 align="center">gptme</h1>

<p align="center">
<i>/ʤiː piː tiː miː/</i>
<br>
<sub><a href="https://gptme.org/docs/misc/acronyms.html">what does it stand for?</a></sub>
</p>

<!-- Links -->
<p align="center">
  <a href="https://gptme.org/docs/getting-started.html">Getting Started</a>
  •
  <a href="https://gptme.org/">Website</a>
  •
  <a href="https://gptme.org/docs/">Documentation</a>
</p>

<!-- Badges -->
<p align="center">
  <a href="https://github.com/gptme/gptme/actions/workflows/build.yml">
    <img src="https://github.com/gptme/gptme/actions/workflows/build.yml/badge.svg" alt="Build Status" />
  </a>
  <a href="https://github.com/gptme/gptme/actions/workflows/docs.yml">
    <img src="https://github.com/gptme/gptme/actions/workflows/docs.yml/badge.svg" alt="Docs Build Status" />
  </a>
  <a href="https://codecov.io/gh/gptme/gptme">
    <img src="https://codecov.io/gh/gptme/gptme/graph/badge.svg?token=DYAYJ8EF41" alt="Codecov" />
  </a>
  <br>
  <a href="https://pypi.org/project/gptme/">
    <img src="https://img.shields.io/pypi/v/gptme" alt="PyPI version" />
  </a>
  <a href="https://pepy.tech/project/gptme">
    <img src="https://img.shields.io/pepy/dt/gptme" alt="PyPI - Downloads all-time" />
  </a>
  <a href="https://pypistats.org/packages/gptme">
    <img src="https://img.shields.io/pypi/dd/gptme?color=success" alt="PyPI - Downloads per day" />
  </a>
  <br>
  <a href="https://discord.gg/NMaCmmkxWv">
    <img src="https://img.shields.io/discord/1271539422017618012?logo=discord&style=social" alt="Discord" />
  </a>
  <a href="https://x.com/gptmeorg">
    <img src="https://img.shields.io/twitter/follow/gptmeorg?style=social" alt="X.com" />
  </a>
  <br>
  <a href="https://gptme.org/docs/projects.html">
    <img src="https://img.shields.io/badge/powered%20by-gptme%20%F0%9F%A4%96-5151f5?style=flat" alt="Powered by gptme" />
  </a>
</p>

<p align="center">
📜 A personal AI agent in your terminal, with tools to:<br/>
run shell commands, write code, edit files, browse the web, use vision, and much more.<br/>
A great coding agent, but general-purpose enough to assist in all kinds of knowledge-work.
</p>

<p align="center">
An unconstrained local free and open-source <a href="https://gptme.org/docs/alternatives.html">alternative</a> to Claude Code, Codex, Cursor Agents, etc.<br/>
One of the first agent CLIs created (Spring 2023) — and still in very active development.
</p>

## 📚 Table of Contents

- 📢 [News](#news)
- 🎥 [Demos](#-demos)
- 🌟 [Features](#-features)
  - [🛠 Tools](#-tools)
  - [🔌 Extensibility: Plugins, Skills & Lessons](#-extensibility-plugins-skills--lessons)
  - [🔗 Integrations: MCP & ACP](#-integrations-mcp--acp)
  - [🤖 Autonomous Agents](#-autonomous-agents)
  - [🛠 Use Cases](#-use-cases)
  - [🛠 Developer Perks](#-developer-perks)
  - [🚧 In Progress](#-in-progress)
- 🚀 [Getting Started](#-getting-started)
- 🛠 [Usage](#-usage)
- 🌍 [Ecosystem](#-ecosystem)
- 💬 [Community](#-community)
- 📊 [Stats](#-stats)
- 🔗 [Links](#-links)

## 📢 News

- **Coming soon** - [gptme.ai](https://gptme.ai) service for running agents in the cloud; [gptme desktop](https://github.com/gptme/gptme-tauri) app for easy local use.
- **2026-01** - [gptme-agent-template](https://github.com/gptme/gptme-agent-template) v0.4: [Bob](https://github.com/TimeToBuildBob) reaches 1000+ autonomous sessions, autonomous run loops, enhanced context generation
- **2025-12** - [v0.31.0](https://github.com/gptme/gptme/releases/tag/v0.31.0): Background jobs, form tool, cost tracking, content-addressable storage
- **2025-11** - [v0.30.0](https://github.com/gptme/gptme/releases/tag/v0.30.0): Plugin system, context compression, subagent planner mode
- **2025-10** - [v0.29.0](https://github.com/gptme/gptme/releases/tag/v0.29.0): Lessons system for contextual guidance, MCP discovery & dynamic loading, token awareness; [Bob](https://github.com/TimeToBuildBob) begins autonomous runs with GitHub monitoring
- **2025-08** - [v0.28.0](https://github.com/gptme/gptme/releases/tag/v0.28.0): MCP support, morph tool for fast edits, auto-commit, redesigned server API
- **2025-03** - [v0.27.0](https://github.com/gptme/gptme/releases/tag/v0.27.0): Pre-commit integration, macOS computer use, Claude 3.7 Sonnet, DeepSeek R1, local TTS with Kokoro
- **2025-01** - [gptme-contrib](https://github.com/gptme/gptme-contrib) created: community plugins including Twitter/X, Discord bot, email tools, consortium (multi-agent)
- **2024-12** - [gptme-agent-template](https://github.com/gptme/gptme-agent-template) v0.3: Template for persistent agents
- **2024-11** - Ecosystem expansion: [gptme-webui](https://github.com/gptme/gptme-webui), [gptme-rag](https://github.com/gptme/gptme-rag), [gptme.vim](https://github.com/gptme/gptme.vim), [Bob](https://github.com/TimeToBuildBob) created (first autonomous agent)
- **2024-10** - [First viral tweet](https://x.com/rohanpaul_ai/status/1841999030999470326) bringing widespread attention
- **2024-08** - [Show HN](https://news.ycombinator.com/item?id=41204256), Anthropic Claude support, tmux tool
- **2023-09** - [Initial public release](https://news.ycombinator.com/item?id=37394845) on HN, [Reddit](https://www.reddit.com/r/LocalLLaMA/comments/16atlia/), [Twitter](https://x.com/ErikBjare/status/1699097896451289115)
- **2023-03** - [Initial commit](https://github.com/gptme/gptme/commit/d00e9aae68cbd6b89bbc474ed7721d08796dc) - one of the first agent CLIs


<!-- source of truth: docs/timeline.rst and docs/changelog.rst -->
For more history, see the [Timeline](https://gptme.org/docs/timeline.html) and [Changelog](https://gptme.org/docs/changelog.html).

## 🎥 Demos

> [!NOTE]
> The screencasts below are from 2023. gptme has evolved a lot since then!
> For up-to-date examples and screenshots, see the [Documentation][docs-examples].
> We're working on automated demo generation: [#1554](https://github.com/gptme/gptme/issues/1554).

<table>
  <tr>
    <th>Fibonacci</th>
    <th>Snake with curses</th>
  </tr>
  <tr>
    <td width="50%">

[![demo screencast with asciinema](https://github.com/ErikBjare/gptme/assets/1405370/5dda4240-bb7d-4cfa-8dd1-cd1218ccf571)](https://asciinema.org/a/606375)

  <details>
  <summary>Steps</summary>
  <ol>
    <li> Create a new dir 'gptme-test-fib' and git init
    <li> Write a fib function to fib.py, commit
    <li> Create a public repo and push to GitHub
  </ol>
  </details>

  </td>

  <td width="50%">

[![621992-resvg](https://github.com/ErikBjare/gptme/assets/1405370/72ac819c-b633-495e-b20e-2e40753ec376)](https://asciinema.org/a/621992)

  <details>
  <summary>Steps</summary>
  <ol>
    <li> Create a snake game with curses to snake.py
    <li> Running fails, ask gptme to fix a bug
    <li> Game runs
    <li> Ask gptme to add color
    <li> Minor struggles
    <li> Finished game with green snake and red apple pie!
  </ol>
  </details>
  </td>
</tr>

<tr>
  <th>Mandelbrot with curses</th>
  <th>Answer question from URL</th>
</tr>
<tr>
  <td width="50%">

[![mandelbrot-curses](https://github.com/ErikBjare/gptme/assets/1405370/570860ac-80bd-4b21-b8d1-da187d7c1a95)](https://asciinema.org/a/621991)

  <details>
  <summary>Steps</summary>
  <ol>
    <li> Render mandelbrot with curses to mandelbrot_curses.py
    <li> Program runs
    <li> Add color
  </ol>
  </details>

  </td>

  <td width="25%">

[![superuserlabs-ceo](https://github.com/ErikBjare/gptme/assets/1405370/bae45488-f4ed-409c-a656-0c5218877de2)](https://asciinema.org/a/621997)

  <details>
  <summary>Steps</summary>
  <ol>
    <li> Ask who the CEO of Superuser Labs is, passing website URL
    <li> gptme browses the website, and answers correctly
  </ol>
  </details>
  </td>
  </tr>

  <tr>
    <th>Terminal UI</th>
    <th>Web UI</th>
  </tr>
  <tr>
  <td width="50%">

<!--[![terminal-ui](https://github.com/ErikBjare/gptme/assets/1405370/terminal-ui-demo)](https://asciinema.org/a/terminal-demo)-->

  <details>
  <summary>Features</summary>
  <ul>
    <li> Powerful terminal interface
    <li> Convenient CLI commands
    <li> Diff & Syntax highlighting
    <li> Tab completion
    <li> Command history
  </ul>
  </details>

  </td>
  <td width="50%">

<!--[![web-ui](https://github.com/ErikBjare/gptme/assets/1405370/web-ui-demo)](https://chat.gptme.org)-->

  <details>
  <summary>Features</summary>
  <ul>
    <li> Chat with gptme from your browser
    <li> Access to all tools and features
    <li> Modern, responsive interface
    <li> Self-hostable
    <li> Available at <a href="https://chat.gptme.org">chat.gptme.org</a>
  </ul>
  </details>

  </td>
  </tr>
</table>

You can find more [Demos][docs-demos] and [Examples][docs-examples] in the [documentation][docs].

## 🌟 Features

- 💻 **Code execution**
  - Executes code in your local environment with the [shell][docs-tools-shell] and [python][docs-tools-python] tools.
- 🧩 **Read, write, and change files**
  - Makes incremental changes with the [patch][docs-tools-patch] tool.
- 🌐 **Search and browse the web**
  - Can use a browser via Playwright with the [browser][docs-tools-browser] tool.
- 👀 **Vision**
  - Can see images referenced in prompts, screenshots of your desktop, and web pages.
- 🔄 **Self-correcting**
  - Output is fed back to the assistant, allowing it to respond and self-correct.
- 📚 **[Lessons system][docs-lessons]**
  - Contextual guidance and best practices automatically included when relevant.
  - Keyword, tool, and pattern-based matching.
  - Adapts to interactive vs autonomous modes.
  - Extend with your own lessons and [skills][docs-skills].
- 🤖 **Support for many LLM [providers][docs-providers]**
  - Anthropic (Claude), OpenAI (GPT), Google (Gemini), xAI (Grok), DeepSeek, and more.
  - Use OpenRouter for access to 100+ models, or serve locally with `llama.cpp`.
- 🌐 **Web UI and REST API**
  - Modern web interface at [chat.gptme.org](https://chat.gptme.org) ([gptme-webui])
  - Simple built-in web UI included in the Python package.
  - [Server][docs-server] with REST API.
  - Standalone executable builds available with PyInstaller.
- 💻 **[Computer use][docs-tools-computer]** (see [#216](https://github.com/gptme/gptme/issues/216))
  - Give the assistant access to a full desktop, allowing it to interact with GUI applications.
- 🗣️ **[Text-to-Speech][docs-tools-tts]** — locally generated using Kokoro (no cloud required).
- 🔊 **Tool sounds** — pleasant notification sounds for different tool operations.
  - Enable with `GPTME_TOOL_SOUNDS=true`.

### 🛠 Tools

gptme equips the AI with a rich set of built-in tools:

| Tool | Description |
|------|-------------|
| `shell` | Execute shell commands directly in your terminal |
| `ipython` | Run Python code with access to your installed libraries |
| `read` | Read files and directories |
| `save` / `append` | Create or update files |
| `patch` / `morph` | Make incremental edits to existing files |
| `browser` | Search and navigate the web via Playwright |
| `vision` | Process and analyze images |
| `screenshot` | Capture screenshots of your desktop |
| `rag` | Retrieve context from local files (Retrieval Augmented Generation) |
| `gh` | Interact with GitHub via the GitHub CLI |
| `tmux` | Run long-lived commands in persistent terminal sessions |
| `computer` | Full desktop access for GUI interactions |
| `subagent` | Spawn sub-agents for parallel or isolated tasks |
| `chats` | Reference and search past conversations |
| `tts` | Text-to-speech output |
| `youtube` | Fetch and process YouTube video transcripts |

Use `/tools` during a conversation to see all available tools and their status.

### 🔌 Extensibility: Plugins, Skills & Lessons

gptme has a layered extensibility system that lets you tailor it to your workflow:

**[Plugins][docs-plugins]** — extend gptme with custom tools, hooks, and commands via Python packages:

```toml
# gptme.toml
[plugins]
paths = ["~/.config/gptme/plugins", "./plugins"]
enabled = ["my_plugin"]
```

**[Skills][docs-skills]** — lightweight workflow bundles (Anthropic format) that auto-load when mentioned by name. Great for packaging reusable instructions and helper scripts without writing Python.

**[Lessons][docs-lessons]** — contextual guidance that auto-injects into conversations based on keywords, tools, and patterns. Write your own to capture team best-practices or domain knowledge.

**[Hooks][docs-hooks]** — run custom code at key lifecycle events (before/after tool calls, on conversation start, etc.) without a full plugin.

**[gptme-contrib][gptme-contrib]** — community-contributed plugins, packages, scripts, and lessons:

| Plugin | Description |
|--------|-------------|
| [gptme-consortium](https://github.com/gptme/gptme-contrib/tree/master/plugins/gptme-consortium) | Multi-model consensus decision-making |
| [gptme-imagen](https://github.com/gptme/gptme-contrib/tree/master/plugins/gptme-imagen) | Multi-provider image generation |
| [gptme-lsp](https://github.com/gptme/gptme-contrib/tree/master/plugins/gptme-lsp) | Language Server Protocol integration |
| [gptme-ace](https://github.com/gptme/gptme-contrib/tree/master/plugins/gptme-ace) | ACE-inspired context optimization |
| [gptme-gupp](https://github.com/gptme/gptme-contrib/tree/master/plugins/gptme-gupp) | Work state persistence across sessions |

### 🔗 Integrations: MCP & ACP

**[MCP (Model Context Protocol)][docs-mcp]** — use any MCP server as a tool source:

```sh
pipx install gptme  # MCP support included by default
```

gptme can discover and dynamically load MCP servers, giving the agent access to databases, APIs, file systems, and any other MCP-compatible tool. See the [MCP docs][docs-mcp] for server configuration.

**[ACP (Agent Client Protocol)][docs-acp]** — use gptme as a coding agent directly from your editor:

```sh
pipx install 'gptme[acp]'
```

This makes gptme available as a drop-in coding agent in [Zed](https://zed.dev/) and JetBrains IDEs. Your editor sends requests, gptme executes with its full toolset (shell, browser, files, etc.) and streams results back.

### 🤖 Autonomous Agents

gptme is designed to run not just interactively but as a **persistent autonomous agent**. The [gptme-agent-template][agent-template] provides a complete scaffold for building your own:

- **Persistent workspace** — git-tracked brain across sessions
- **Run loops** — scheduled or event-driven autonomous operation
- **Task management** — structured task queue with GTD-style metadata
- **Meta-learning** — lessons system captures patterns and improves over time
- **Multi-agent coordination** — file leases, message bus, and work claiming for concurrent agents

[Bob](https://github.com/TimeToBuildBob) is the reference implementation — an autonomous AI agent that has completed 1000+ sessions, contributes to open source, and manages its own tasks. Bob and [Alice](https://github.com/TimeToLearnAlice) are sibling agents forked from the same architecture — each improving themselves and collaborating with each other, hinting at the broader team of AI agents gptme enables.

### 🛠 Use Cases

- 🖥 **Development:** Write and run code faster with AI assistance.
- 🎯 **Shell Expert:** Get the right command using natural language (no more memorizing flags!).
- 📊 **Data Analysis:** Process and analyze data directly in your terminal.
- 🎓 **Interactive Learning:** Experiment with new technologies or codebases hands-on.
- 🤖 **Agents & Tools:** Build long-running autonomous agents for real work.
- 🔬 **Research:** Automate literature review, data collection, and analysis pipelines.

### 🛠 Developer Perks

- ⭐ One of the first agent CLIs created (Spring 2023) that is still in active development.
- 🧰 **Easy to extend**
  - Most functionality can be implemented with [tools][docs-tools], [hooks][docs-hooks], and [commands][docs-commands].
  - [Plugins][docs-plugins] allow for easy packaging of extensions.
  - Trying to stay [tiny][docs-arewetiny] — minimal core, extend as needed.
- 🧪 Extensive testing, high coverage.
- 🧹 Clean codebase, checked and formatted with `mypy`, `ruff`, and `pyupgrade`.
- 🤖 [GitHub Bot][docs-bot] to request changes from comments! (see [#16](https://github.com/gptme/gptme/issues/16))
  - Operates in this repo! (see [#18](https://github.com/gptme/gptme/issues/18) for example)
  - Runs entirely in GitHub Actions.
- 📊 [Evaluation suite][docs-evals] for testing capabilities of different models.
- 📝 [gptme.vim][gptme.vim] for easy integration with vim.

### 🚧 In Progress

- 🖥 **[gptme-tauri](https://github.com/gptme/gptme-tauri)** — desktop app wrapping gptme for easy local use (WIP)
- ☁️ **[gptme.ai](https://gptme.ai)** — managed cloud service for running gptme agents (WIP; still self-hostable by running `gptme-server` + `gptme-webui` yourself)
- 🌳 Tree-based conversation structure (see [#17](https://github.com/gptme/gptme/issues/17))
- 📜 RAG to automatically include context from local files (see [#59](https://github.com/gptme/gptme/issues/59))
- 🏆 Advanced evals for testing frontier capabilities

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or newer
- An API key for at least one LLM provider:
  - [Anthropic](https://console.anthropic.com/) (set `ANTHROPIC_API_KEY`)
  - [OpenAI](https://platform.openai.com/) (set `OPENAI_API_KEY`)
  - [OpenRouter](https://openrouter.ai/) (set `OPENROUTER_API_KEY`)
  - Local models via `llama.cpp` (no key required — see [providers docs][docs-providers])

### Installation

For full setup instructions, see the [Getting Started guide][docs-getting-started].

```sh
# With pipx (recommended, requires Python 3.10+)
pipx install gptme

# With uv
uv tool install gptme

# With optional extras
pipx install 'gptme[browser]'  # Playwright for web browsing
pipx install 'gptme[all]'      # Everything

# Latest from git with all extras
uv tool install 'git+https://github.com/gptme/gptme.git[all]'
```

### Quick Start

```sh
gptme
```

You'll be greeted with a prompt. Type your request and gptme will respond, using tools as needed.

### Example Commands

```sh
# Create a particle effect visualization
gptme 'write an impressive and colorful particle effect using three.js to particles.html'

# Generate visual art
gptme 'render mandelbrot set to mandelbrot.png'

# Get configuration suggestions
gptme 'suggest improvements to my vimrc'

# Process media files
gptme 'convert to h265 and adjust the volume' video.mp4

# Code assistance from git diffs
git diff | gptme 'complete the TODOs in this diff'

# Fix failing tests
make test | gptme 'fix the failing tests'

# Non-interactively run a task
gptme -y 'run the test suite and fix any failing tests'
```

For more, see the [Getting Started][docs-getting-started] guide and the [Examples][docs-examples] in the [documentation][docs].

### ⚙️ Configuration

Create `~/.config/gptme/config.toml`:

```toml
[user]
name = "User"
about = "I am a curious human programmer."
response_preference = "Don't explain basic concepts"

[prompt]
# Additional files to always include as context
# files = ["~/notes/llm-tips.md"]

[env]
# Set your default model
# MODEL = "anthropic/claude-sonnet-4-20250514"
# MODEL = "openai/gpt-4o"
```

For all options, see the [configuration docs][docs-config].

## 🛠 Usage

```sh
$ gptme --help
Usage: gptme [OPTIONS] [PROMPTS]...

  gptme is a chat-CLI for LLMs, empowering them with tools to run shell
  commands, execute code, read and manipulate files, and more.

  If PROMPTS are provided, a new conversation will be started with it. PROMPTS
  can be chained with the '-' separator.

  The interface provides user commands that can be used to interact with the
  system.

  Available commands:
    /undo         Undo the last action
    /log          Show the conversation log
    /edit         Edit the conversation in your editor
    /rename       Rename the conversation
    /fork         Create a copy of the conversation
    /summarize    Summarize the conversation
    /replay       Replay tool operations
    /export       Export conversation as HTML
    /model        Show or switch the current model
    /models       List available models
    /tokens       Show token usage and costs
    /context      Show context token breakdown
    /tools        Show available tools
    /commit       Ask assistant to git commit
    /compact      Compact the conversation
    /impersonate  Impersonate the assistant
    /restart      Restart gptme process
    /setup        Setup gptme
    /help         Show this help message
    /exit         Exit the program

  See docs for all commands: https://gptme.org/docs/commands.html

  Keyboard shortcuts:
    Ctrl+X Ctrl+E  Edit prompt in your editor
    Ctrl+J         Insert a new line without executing the prompt

Options:
  --name TEXT            Name of conversation. Defaults to generating a random
                         name.
  -m, --model TEXT       Model to use, e.g. openai/gpt-5, anthropic/claude-
                         sonnet-4-20250514. If only provider given then a
                         default is used.
  -w, --workspace TEXT   Path to workspace directory. Pass '@log' to create a
                         workspace in the log directory.
  --agent-path TEXT      Path to agent workspace directory.
  -r, --resume           Load most recent conversation.
  -y, --no-confirm       Skip all confirmation prompts.
  -n, --non-interactive  Non-interactive mode. Implies --no-confirm.
  --system TEXT          System prompt. Options: 'full', 'short', or something
                         custom.
  -t, --tools TEXT       Tools to allow as comma-separated list. Available:
                         append, browser, chats, choice, computer, gh,
                         ipython, morph, patch, rag, read, save, screenshot,
                         shell, subagent, tmux, tts, vision, youtube.
  --tool-format TEXT     Tool format to use. Options: markdown, xml, tool
  --no-stream            Don't stream responses
  --show-hidden          Show hidden system messages.
  -v, --verbose          Show verbose output.
  --version              Show version and configuration information
  --help                 Show this message and exit.
```

## 🌍 Ecosystem

gptme is more than a CLI — it's a platform with a growing ecosystem:

| Project | Description |
|---------|-------------|
| [gptme-webui] | Modern React web interface, available at [chat.gptme.org](https://chat.gptme.org) |
| [gptme-contrib] | Community plugins, packages, scripts, and lessons |
| [gptme-agent-template][agent-template] | Template for building persistent autonomous agents |
| [gptme-rag] | RAG integration for semantic search over local files |
| [gptme.vim] | Vim plugin for in-editor gptme integration |
| [gptme-tauri] | Desktop app (WIP) |
| [gptme.ai](https://gptme.ai) | Managed cloud service (WIP) |

**Community agents powered by gptme:**
- [Bob](https://github.com/TimeToBuildBob) — autonomous AI agent, 1000+ sessions, contributes to open source
- [Alice](https://github.com/TimeToLearnAlice) — sibling agent forked from the same architecture, collaborates with Bob

## 💬 Community

- **[Discord][discord]** — ask questions, share what you've built, discuss features
- **[GitHub Discussions](https://github.com/gptme/gptme/discussions)** — longer-form conversation and ideas
- **[X/Twitter](https://x.com/gptmeorg)** — updates and announcements

Contributions welcome! See the [contributing guide](https://gptme.org/docs/contributing.html).

## 📊 Stats

### ⭐ Stargazers over time

[![Stargazers over time](https://starchart.cc/gptme/gptme.svg)](https://starchart.cc/gptme/gptme)

### 📈 Download Stats

- [PePy][pepy]
- [PyPiStats][pypistats]

[pepy]: https://pepy.tech/project/gptme
[pypistats]: https://pypistats.org/packages/gptme

## 🔗 Links

- [Website][website]
- [Documentation][docs]
- [GitHub][github]
- [Discord][discord]

<!-- links -->

[website]: https://gptme.org/
[discord]: https://discord.gg/NMaCmmkxWv
[github]: https://github.com/gptme/gptme
[gptme.vim]: https://github.com/gptme/gptme.vim
[gptme-webui]: https://github.com/gptme/gptme-webui
[gptme-rag]: https://github.com/gptme/gptme-rag
[gptme-contrib]: https://github.com/gptme/gptme-contrib
[gptme-tauri]: https://github.com/gptme/gptme-tauri
[agent-template]: https://github.com/gptme/gptme-agent-template
[bob]: https://github.com/TimeToBuildBob
[docs]: https://gptme.org/docs/
[docs-getting-started]: https://gptme.org/docs/getting-started.html
[docs-examples]: https://gptme.org/docs/examples.html
[docs-demos]: https://gptme.org/docs/demos.html
[docs-providers]: https://gptme.org/docs/providers.html
[docs-tools]: https://gptme.org/docs/tools.html
[docs-tools-python]: https://gptme.org/docs/tools.html#python
[docs-tools-shell]: https://gptme.org/docs/tools.html#shell
[docs-tools-patch]: https://gptme.org/docs/tools.html#patch
[docs-tools-browser]: https://gptme.org/docs/tools.html#browser
[docs-tools-computer]: https://gptme.org/docs/tools.html#computer
[docs-tools-tts]: https://gptme.org/docs/tools.html#tts
[docs-lessons]: https://gptme.org/docs/lessons.html
[docs-skills]: https://gptme.org/docs/skills.html
[docs-bot]: https://gptme.org/docs/bot.html
[docs-server]: https://gptme.org/docs/server.html
[docs-evals]: https://gptme.org/docs/evals.html
[docs-config]: https://gptme.org/docs/config.html
[docs-arewetiny]: https://gptme.org/docs/arewetiny.html
[docs-plugins]: https://gptme.org/docs/plugins.html
[docs-hooks]: https://gptme.org/docs/hooks.html
[docs-commands]: https://gptme.org/docs/commands.html
[docs-mcp]: https://gptme.org/docs/mcp.html
[docs-acp]: https://gptme.org/docs/acp.html
[anthropic-computer-use]: https://www.anthropic.com/news/3-5-models-and-computer-use
