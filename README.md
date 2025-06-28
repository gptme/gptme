<div align="center">
  <img src="https://gptme.org/media/logo.png" width=150 />
  <h1>gptme</h1>
  <p><i>/ʤiː piː tiː miː/</i></p>
  
  <p>
    <a href="https://gptme.org/docs/getting-started.html">Getting Started</a>
    •
    <a href="https://gptme.org/">Website</a>
    •
    <a href="https://gptme.org/docs/">Documentation</a>
  </p>

  <p>
    <a href="https://github.com/gptme/gptme/actions/workflows/build.yml"><img src="https://github.com/gptme/gptme/actions/workflows/build.yml/badge.svg" alt="Build Status" /></a>
    <a href="https://github.com/gptme/gptme/actions/workflows/docs.yml"><img src="https://github.com/gptme/gptme/actions/workflows/docs.yml/badge.svg" alt="Docs Build Status" /></a>
    <a href="https://codecov.io/gh/gptme/gptme"><img src="https://codecov.io/gh/gptme/gptme/graph/badge.svg?token=DYAYJ8EF41" alt="Codecov" /></a>
    <a href="https://pypi.org/project/gptme/"><img src="https://img.shields.io/pypi/v/gptme" alt="PyPI version" /></a>
    <a href="https://pepy.tech/project/gptme"><img src="https://img.shields.io/pepy/dt/gptme" alt="PyPI - Downloads all-time" /></a>
    <a href="https://pypistats.org/packages/gptme"><img src="https://img.shields.io/pypi/dd/gptme?color=success" alt="PyPI - Downloads per day" /></a>
  </p>

  <p>
    <a href="https://discord.gg/NMaCmmkxWv"><img src="https://img.shields.io/discord/1271539422017618012?logo=discord&style=social" alt="Discord" /></a>
    <a href="https://x.com/gptmeorg"><img src="https://img.shields.io/twitter/follow/gptmeorg?style=social" alt="X.com" /></a>
  </p>

  <p>
    <a href="https://gptme.org/docs/projects.html"><img src="https://img.shields.io/badge/powered%20by-gptme%20%F0%9F%A4%96-5151f5?style=flat" alt="Powered by gptme" /></a>
  </p>
</div>

<p align="center">
  <strong>Your personal AI assistant/agent in the terminal</strong><br>
  Use the shell, run code, edit files, browse the web, and much more.<br>
  A powerful coding companion and general-purpose assistant for all kinds of knowledge work.
</p>

## Why gptme?

- 🚀 **Unconstrained**: No limitations on software, internet access, or timeouts
- 🔒 **Privacy-focused**: Use local models for complete data control
- 🛠 **Versatile**: Great for coding, but capable of assisting with any task
- 🧠 **Intelligent**: Leverages advanced LLMs for human-like interactions
- 🔧 **Extensible**: Easy to add new tools and capabilities

An advanced <a href="https://gptme.org/docs/alternatives.html">alternative</a> to ChatGPT with "Code Interpreter", Cursor Agent, and more.

## 📚 Table of Contents

- 🎥 [Demos](#-demos)
- 🌟 [Features](#-features)
- 🚀 [Getting Started](#-getting-started)
- 🛠 [Usage](#-usage)
- 👥 [Community](#-community)
- 📊 [Stats](#-stats)
- 🔗 [Links](#-links)

## 🎥 Demos

> [!NOTE]
> These demos showcase core capabilities. New demos highlighting the latest features are coming soon!
>
> For the most current examples, check our [documentation][docs-examples].

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

### Core Capabilities
- 💻 **Code Execution**: Run Python code and shell commands in your local environment via [ipython][docs-tools-python] and [shell][docs-tools-shell] tools
- 🧩 **File Management**: Read, write, and edit files with precision using [patch][docs-tools-patch] for targeted changes
- 🌐 **Web Access**: Search and browse the web with the [browser][docs-tools-browser] tool powered by Playwright
- 👀 **Vision**: Process images from prompts, take screenshots, and analyze web pages
- 🔄 **Self-correcting**: Feedback loop enables the assistant to learn and improve responses

### Platform & Integrations
- 🤖 **Multiple LLM Providers**: Support for [OpenAI, Anthropic, OpenRouter][docs-providers], and local models via `llama.cpp`
- 🌐 **Web Interface**: Modern UI at [chat.gptme.org](https://chat.gptme.org) ([gptme-webui]) and simple built-in web UI
- 🖥️ **API Access**: [Server][docs-server] with REST API and standalone executable builds
- 💻 **[Computer Use][docs-tools-computer]**: Control desktop applications as demonstrated by [Anthropic][anthropic-computer-use]
- 🤖 **Agent Framework**: Build persistent agents with [gptme-agent-template][agent-template] (see [Bob][bob])

### Developer Experience
- 🧰 **Extensible Architecture**: Most functionality implemented as [tools][docs-tools] for easy extension
- 🧪 **Quality Assurance**: Extensive testing with high coverage
- 🧹 **Clean Code**: Maintained with `mypy`, `ruff`, and `pyupgrade`
- 🤖 **GitHub Integration**: [Bot][docs-bot] to request changes from comments
- 📊 **Evaluation Suite**: [Tools][docs-evals] for testing model capabilities
- 📝 **Editor Integration**: [gptme.vim][gptme.vim] for Vim integration

### User Experience Enhancements
- 🚰 **Flexible Input**: Pipe in context via `stdin` or as arguments
- ⌨️ **Smart UI**: Tab completion and highlighting for commands and paths
- 📝 **Conversation Management**: Automatic naming and organization of chat history
- ✅ **Developer Tools**: Integration with [pre-commit](https://github.com/pre-commit/pre-commit)
- 🗣️ **Accessibility**: [Text-to-Speech][docs-tools-tts] support using Kokoro
- 🎯 **Customization**: Feature flags for advanced usage via [configuration][docs-config]

### 🚧 Coming Soon
- 🌳 Tree-based conversation structure ([#17](https://github.com/gptme/gptme/issues/17))
- 📜 RAG for automatic context from local files ([#59](https://github.com/gptme/gptme/issues/59))
- 🏆 Advanced evaluation framework for frontier capabilities

## 🚀 Getting Started

### Quick Install

```sh
# Requires Python 3.10+
pipx install gptme
```

### Run Your First Command

```sh
gptme
```

### Try These Examples

```sh
# Generate creative code
gptme 'write an impressive particle effect using three.js to particles.html'

# Create visualizations
gptme 'render mandelbrot set to mandelbrot.png'

# Get configuration help
gptme 'suggest improvements to my vimrc'

# Process media files
gptme 'convert to h265 and adjust the volume' video.mp4

# Code assistance
git diff | gptme 'complete the TODOs in this diff'
make test | gptme 'fix the failing tests'
```

For detailed instructions, see our [Getting Started Guide][docs-getting-started] and [Examples][docs-examples].

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
    /model        List or switch models
    /edit         Edit the conversation in your editor
    /rename       Rename the conversation
    /fork         Copy the conversation using a new name
    /summarize    Summarize the conversation
    /replay       Rerun tools in the conversation, won't store output
    /impersonate  Impersonate the assistant
    /tokens       Show the number of tokens used
    /export       Export conversation as HTML
    /help         Show this help message
    /exit         Exit the program

  Keyboard shortcuts:
    Ctrl+X Ctrl+E  Edit prompt in your editor
    Ctrl+J         Insert a new line without executing the prompt

Options:
  --name TEXT            Name of conversation. Defaults to generating a random
                         name.
  -m, --model TEXT       Model to use, e.g. openai/gpt-4o,
                         anthropic/claude-3-7-sonnet-20250219. If only
                         provider given then a default is used.
  -w, --workspace TEXT   Path to workspace directory. Pass '@log' to create a
                         workspace in the log directory.
  -r, --resume           Load last conversation
  -y, --no-confirm       Skips all confirmation prompts.
  -n, --non-interactive  Non-interactive mode. Implies --no-confirm.
  --system TEXT          System prompt. Can be 'full', 'short', or something
                         custom.
  -t, --tools TEXT       Comma-separated list of tools to allow. Available:
                         append, browser, chats, computer, gh, ipython, patch,
                         rag, read, save, screenshot, shell, subagent, tmux,
                         vision.
  --tool-format TEXT     Tool parsing method. Can be 'markdown', 'xml',
                         'tool'. (experimental)
  --no-stream            Don't stream responses
  --show-hidden          Show hidden system messages.
  -v, --verbose          Show verbose output.
  --version              Show version and configuration information
  --help                 Show this message and exit.
```

## 👥 Community

We welcome contributions and feedback from our community! Here's how you can get involved:

- **Bug Reports & Feature Requests**: Submit issues on [GitHub][github]
- **Contribute Code**: See our [contribution guidelines](https://gptme.org/docs/contributing.html)
- **Join Discussions**: Connect with us on [Discord][discord]
- **Stay Updated**: Follow us on [X.com](https://x.com/gptmeorg)
- **Share Your Projects**: Built something with gptme? Let us know!

## 📊 Stats

### ⭐ Stargazers over time

[![Stargazers over time](https://starchart.cc/gptme/gptme.svg)](https://starchart.cc/gptme/gptme)

### 📈 Download Stats

- [PePy][pepy] - Lifetime downloads
- [PyPiStats][pypistats] - Daily download metrics

[pepy]: https://pepy.tech/project/gptme
[pypistats]: https://pypistats.org/packages/gptme

## 🔗 Links

- 🌐 [Website][website]
- 📚 [Documentation][docs]
- 🐙 [GitHub][github]
- 💬 [Discord][discord]

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
[docs-config]: https://gptme.org/docs/config.html
[anthropic-computer-use]: https://www.anthropic.com/news/3-5-models-and-computer-use
