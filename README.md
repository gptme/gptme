<p align="center">
  <img src="https://gptme.org/media/logo.png" width=150 />
</p>

<h1 align="center">gptme</h1>

<p align="center">
<i>/ʤiː piː tiː miː/</i>
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
📜 Personal AI assistant/agent in your terminal, with tools so it can:<br/>Use the terminal, run code, edit files, browse the web, use vision, and much more;<br/>a great coding agent, but is general-purpose to assists in all kinds of knowledge-work, from a simple but powerful CLI.
</p>

<p align="center">
An unconstrained local <a href="https://gptme.org/docs/alternatives.html">alternative</a> to: ChatGPT with "Code Interpreter", Cursor Agent, etc.<br/>Not limited by lack of software, internet access, timeouts, or privacy concerns (if using local models).
</p>

## 📚 Table of Contents

- 🎥 [Demos](#-demos)
- 🌟 [Features](#-features)
- 🚀 [Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Quick Start](#quick-start)
  - [Example Commands](#example-commands)
  - [Configuration](#️-configuration)
- 🧰 [Available Tools](#-available-tools)
- 🛠 [Usage](#-usage)
- ❓ [Troubleshooting](#-troubleshooting)
- 📊 [Stats](#-stats)
- 🔗 [Links](#-links)

## 🎥 Demos

> [!NOTE]
> These demos are from early 2023 and do not reflect the latest capabilities. We're working on new demos for 2025 that showcase the full range of current features!

<table>
  <tr>
    <th>Fibonacci (old)</th>
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

- 💻 Code execution
  - Executes code in your local environment with the [shell][docs-tools-shell] and [python][docs-tools-python] tools.
- 🧩 Read, write, and change files
  - Makes incremental changes with the [patch][docs-tools-patch] tool.
- 🌐 Search and browse the web.
  - Can use a browser via Playwright with the [browser][docs-tools-browser] tool.
- 👀 Vision
  - Can see images referenced in prompts, screenshots of your desktop, and web pages.
- 🔄 Self-correcting
  - Output is fed back to the assistant, allowing it to respond and self-correct.
- 🤖 Support for several LLM [providers][docs-providers]
  - Use OpenAI, Anthropic, OpenRouter, or serve locally with `llama.cpp`
- 🌐 Web UI and REST API
  - Modern web interface at [chat.gptme.org](https://chat.gptme.org) ([gptme-webui])
  - Simple built-in web UI included in the Python package
  - [Server][docs-server] with REST API
- 💻 [Computer use][docs-tools-computer] tool, as hyped by [Anthropic][anthropic-computer-use] (see [#216](https://github.com/gptme/gptme/issues/216))
  - Give the assistant access to a full desktop, allowing it to interact with GUI applications.
- 🤖 Long-running agents and advanced agent architectures (see [#143](https://github.com/gptme/gptme/issues/143) and [#259](https://github.com/gptme/gptme/issues/259))
  - Create your own agent with persistence using [gptme-agent-template][agent-template], like [Bob][bob].
- ✨ Many smaller features to ensure a great experience
  - 🚰 Pipe in context via `stdin` or as arguments.
    - Passing a filename as an argument will read the file and include it as context.
  - → Smart completion and highlighting:
    - Tab completion and highlighting for commands and paths
  - 📝 Automatic naming of conversations
  - ✅ Detects and integrates [pre-commit](https://github.com/pre-commit/pre-commit)
  - 🗣️ [Text-to-Speech][docs-tools-tts] support, locally generated using Kokoro
  - 🎯 Feature flags for advanced usage, see [configuration docs][docs-config]

### 🛠 Use Cases

- 🖥 **Development:** Write and run code faster with AI assistance.
- 🎯 **Shell Expert:** Get the right command using natural language (no more memorizing flags!).
- 📊 **Data Analysis:** Process and analyze data directly in your terminal.
- 🎓 **Interactive Learning:** Experiment with new technologies or codebases hands-on.
- 🤖 **Agents & Tools:** Experiment with agents & tools in a local environment.

### 🧰 Available Tools

gptme empowers AI with a variety of tools to interact with your system:

| Tool | Description |
|------|-------------|
| `shell` | Execute shell commands directly in your terminal |
| `python` | Run Python code with access to installed libraries |
| `save` | Create or overwrite files on your system |
| `patch` | Make incremental changes to files without rewriting them |
| `browser` | Search and navigate the web through Playwright |
| `vision` | Process and analyze images |
| `screenshot` | Capture screenshots of your desktop |
| `rag` | Retrieve context from your local files (Retrieval Augmented Generation) |
| `gh` | Interact with GitHub repositories through the GitHub CLI |
| `tmux` | Run long-lived commands in persistent sessions |
| `computer` | Access a full desktop environment for GUI interactions |

Use the `/tools` command during a conversation to see all available tools.

### 🛠  Developer perks

- 🧰 Easy to extend
  - Most functionality is implemented as [tools][docs-tools], making it easy to add new features.
- 🧪 Extensive testing, high coverage.
- 🧹 Clean codebase, checked and formatted with `mypy`, `ruff`, and `pyupgrade`.
- 🤖 [GitHub Bot][docs-bot] to request changes from comments! (see [#16](https://github.com/gptme/gptme/issues/16))
  - Operates in this repo! (see [#18](https://github.com/gptme/gptme/issues/18) for example)
  - Runs entirely in GitHub Actions.
- 📊 [Evaluation suite][docs-evals] for testing capabilities of different models
- 📝 [gptme.vim][gptme.vim] for easy integration with vim

### 🚧 In progress

- 🌳 Tree-based conversation structure (see [#17](https://github.com/gptme/gptme/issues/17))
- 📜 RAG to automatically include context from local files (see [#59](https://github.com/gptme/gptme/issues/59))
- 🏆 Advanced evals for testing frontier capabilities

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or newer
- An API key for at least one of the supported LLM providers:
  - OpenAI (set `OPENAI_API_KEY` environment variable)
  - Anthropic (set `ANTHROPIC_API_KEY` environment variable)
  - OpenRouter (set `OPENROUTER_API_KEY` environment variable)
  - Local models via llama.cpp (see [providers documentation][docs-providers])

### Installation

Install with pipx (recommended):

```sh
pipx install gptme
```

Or with pip:

```sh
pip install gptme
```

### Quick Start

To begin a conversation with gptme, simply run:

```sh
gptme
```

You'll be prompted to choose an LLM provider and set up your API key if you haven't already.

### Example Commands

Here are some powerful examples to try:

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
```

For more detailed guidance, see the [Getting Started][docs-getting-started] guide and the [Examples][docs-examples] in the [documentation][docs].

### ⚙️ Configuration

gptme can be configured through environment variables or a configuration file:

#### Configuration File

Create a `~/.config/gptme/config.toml` file with your preferences:

```toml
# Default model and provider
default_model = "openai/gpt-4o"
# default_model = "anthropic/claude-3-5-sonnet-20240620"
# default_model = "openrouter/gpt-4-grok"
# default_model = "llama.cpp/llama-3-70b-q4_0"

# Always confirm before executing shell commands
confirm_shell = true

# Use colored output
color = true

# Default system prompt type ("full", "short", or "custom")
system_prompt = "full"

# Custom path for conversation logs
# logs_dir = "~/gptme-logs"
```

#### Environment Variables

Set these environment variables to configure gptme:

```sh
# API keys
export OPENAI_API_KEY="your-api-key"
export ANTHROPIC_API_KEY="your-api-key"
export OPENROUTER_API_KEY="your-api-key"

# Default model
export GPTME_DEFAULT_MODEL="anthropic/claude-3-7-sonnet-20250219"

# Enable/disable features
export GPTME_CONFIRM_SHELL="true"
export GPTME_COLOR="true"
```

For all configuration options, see the [configuration documentation][docs-config].



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
    /tools        Show available tools
    /edit         Edit the conversation in your editor
    /rename       Rename the conversation
    /fork         Create a copy of the conversation with a new name
    /summarize    Summarize the conversation
    /replay       Re-execute codeblocks in the conversation, wont store output in log
    /impersonate  Impersonate the assistant
    /tokens       Show the number of tokens used
    /export       Export conversation as standalone HTML
    /help         Show this help message
    /exit         Exit the program

  Keyboard shortcuts:
    Ctrl+J        Insert a new line without executing the prompt

Options:
  -n, --name TEXT        Name of conversation. Defaults to generating a random
                         name.
  -m, --model TEXT       Model to use, e.g. openai/gpt-4o,
                         anthropic/claude-3-5-sonnet-20240620. If only
                         provider given, a default is used.
  -w, --workspace TEXT   Path to workspace directory. Pass '@log' to create a
                         workspace in the log directory.
  -r, --resume           Load last conversation
  -y, --no-confirm       Skips all confirmation prompts.
  -n, --non-interactive  Force non-interactive mode. Implies --no-confirm.
  --system TEXT          System prompt. Can be 'full', 'short', or something
                         custom.
  -t, --tools TEXT       Comma-separated list of tools to allow. Available:
                         read, save, append, patch, shell, subagent, tmux,
                         browser, gh, chats, screenshot, vision, computer,
                         python.
  --no-stream            Don't stream responses
  --show-hidden          Show hidden system messages.
  -v, --verbose          Show verbose output.
  --version              Show version and configuration information
  --help                 Show this message and exit.
```


## ❓ Troubleshooting

### Common Issues

**API Key Issues**
- If you get authentication errors, verify your API key is correctly set in your environment variables or config file
- For OpenAI keys, ensure you have sufficient credits and your account is in good standing

**Tool Execution Problems**
- For browser tool errors, ensure Playwright is installed: `pip install playwright && python -m playwright install`
- For GitHub tool issues, verify the GitHub CLI is installed and you're authenticated: `gh auth login`

**Performance Considerations**
- If responses are slow, try using a smaller model or a different provider
- Large conversations consume more tokens; start a new conversation for unrelated tasks

Need more help? Join our [Discord community][discord] or [open an issue][github-issues] on GitHub.

[github-issues]: https://github.com/gptme/gptme/issues

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
[docs-bot]: https://gptme.org/docs/bot.html
[docs-server]: https://gptme.org/docs/server.html
[docs-evals]: https://gptme.org/docs/evals.html
[docs-server]: https://gptme.org/docs/server.html
[docs-config]: https://gptme.org/docs/config.html
[anthropic-computer-use]: https://www.anthropic.com/news/3-5-models-and-computer-use
