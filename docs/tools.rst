Tools
=====

gptme's tools enable AI agents to execute code, edit files, browse the web, process images, and interact with your computer.

Overview
--------

📁 File System
^^^^^^^^^^^^^^

- `Read`_ - Read files in any format
- `Save`_ - Create and overwrite files
- `Patch`_ - Apply precise changes to existing files
- `Morph`_ - Apply fast targeted edits using Morph Fast Apply

💻 Code & Development
^^^^^^^^^^^^^^^^^^^^^

- `Python`_ - Execute Python code interactively with full library access
- `Shell`_ - Run shell commands and manage system processes
- `GH`_ - Interact with GitHub issues, PRs, and repositories
- `Precommit`_ - Automatically run pre-commit checks after file saves
- `Autocommit`_ - Automatically prompt for git commits after file modifications

🌐 Web & Research
^^^^^^^^^^^^^^^^^

- `Browser`_ - Browse websites, take screenshots, and read web content
- `RAG`_ - Index and search through documentation and codebases
- `Chats`_ - Search past conversations for context and references

👁️ Visual & Interactive
^^^^^^^^^^^^^^^^^^^^^^^

- `Vision`_ - Analyze images, diagrams, and visual content
- `Screenshot`_ - Capture your screen for visual context
- `Computer`_ - Control desktop applications through visual interface

🤝 User Interaction
^^^^^^^^^^^^^^^^^^^

- `Choice`_ - Present multiple-choice options to the user
- `Elicit`_ - Request structured single-field input from the user
- `Form`_ - Present a multi-field form for structured user input

⚡ Advanced Workflows
^^^^^^^^^^^^^^^^^^^^^

- `Tmux`_ - Manage long-running processes in terminal sessions
- `Subagent`_ - Delegate subtasks to specialized agent instances
- `Complete`_ - Signal that the autonomous session is finished
- `Restart`_ - Restart the gptme process after configuration changes
- `Vent`_ - Emit in-the-moment friction signals to a durable ledger

🧠 Knowledge & Planning
^^^^^^^^^^^^^^^^^^^^^^^

- `Lessons`_ - Access contextual lessons and behavioral guidance
- `Todo`_ - Manage a conversation-scoped working memory task list

🔌 Extensions
^^^^^^^^^^^^^

- `MCP`_ - Discover and connect Model Context Protocol servers

Combinations
^^^^^^^^^^^^

The real power emerges when tools work together:

- **Web Research + Code**: `Browser`_ + `Python`_ - Browse documentation and implement solutions
- **Visual Development**: `Vision`_ + `Patch`_ - Analyze UI mockups and update code accordingly
- **System Automation**: `Shell`_ + `Python`_ - Combine system commands with data processing
- **Interactive Debugging**: `Screenshot`_ + `Computer`_ - Visual debugging and interface automation
- **Knowledge-Driven Development**: `RAG`_ + `Chats`_ - Learn from documentation and past conversations

Shell
-----

.. automodule:: gptme.tools.shell
    :members:
    :noindex:

Python
------

.. automodule:: gptme.tools.python
    :members:
    :noindex:

Tmux
----

.. automodule:: gptme.tools.tmux
    :members:
    :noindex:

Subagent
--------

.. automodule:: gptme.tools.subagent
    :members:
    :noindex:

Subagent Isolation Contract
^^^^^^^^^^^^^^^^^^^^^^^^^^^

When spawning a subagent you need to know exactly what it inherits from the parent
and what it starts fresh. There are four dimensions:

**1. Workspace config loading**

*Thread mode* (default, ``use_subprocess=False``):
  The subagent inherits the parent's *already-assembled* workspace context —
  the ``[prompt] files`` from ``gptme.toml`` and the ``context_cmd`` output as
  they were loaded for the parent session. It does **not** re-read from the
  subagent's working directory, so a subdirectory with its own ``gptme.toml``
  will not be picked up automatically.

*Subprocess mode* (``use_subprocess=True``):
  Spawns a fresh ``gptme`` process with ``workdir`` as the CWD, which naturally
  loads that directory's ``gptme.toml``. Use this when you want subagents to pick
  up directory-local workspace config.

Fine-grained control:

- ``context_mode="selective"`` + ``context_include`` — share only specific components
  (``"agent"``, ``"tools"``, ``"workspace"``) instead of the full workspace.

  Behavior by mode:

  **Thread**: fully supported — filters the inherited context to the specified components.

  **Subprocess**: ``context_mode`` is ignored (the child loads its own workspace from
  ``gptme.toml``); ``context_include=["workspace"]`` maps to the ``--context files``
  CLI flag to include workspace files. Other ``context_include`` values are ignored in
  this mode.

  **ACP**: both parameters are ignored.

- ``context_window=N`` — limit how many inherited context messages are forwarded
  (``0`` = none, ``None`` = all). Thread mode only; ignored in subprocess and ACP.

- ``context_turns=N`` — forward the last N turns of the parent conversation.
  Thread mode only; ignored in subprocess and ACP.

**2. Tool and state inheritance**

By default the subagent starts with the same tool list as the parent (both threads
share the same initial snapshot; contextvars are thread-isolated so the parent's
tool state cannot be mutated by the subagent).

Three ways to restrict tools:

- ``profile="explorer"`` (or any built-in profile) — applies a tool allowlist at spawn
  time. Built-in profiles: ``explorer`` (read-only), ``researcher``, ``developer``
  (full), ``verifier`` (read-only). Note: ``role="verify"`` forces
  ``use_subprocess=True`` and ``isolated=True`` in addition to the verifier profile.

- ``isolated=True`` — runs the subagent in a git worktree so filesystem writes don't
  affect the parent repo. The worktree is auto-cleaned after completion.

- ``redact_secrets=True`` (default) — scrubs common secret patterns (API keys, tokens,
  passwords) from workspace context messages before they reach the subagent.
  Thread-mode only; has no effect in subprocess or ACP modes (the child process's
  own ``gptme.toml`` controls its secret handling).

Signal tools are loaded regardless of allowlist so the subagent can communicate
back. Thread-mode subagents get ``complete``, ``clarify``, and ``progress``.
Subprocess subagents get ``complete`` and ``clarify``; ``progress`` is not loaded
because it depends on the parent's in-process notification queue.

**3. Cancellation and timeout**

- ``max_time`` (seconds) — a watchdog timer that marks the subagent result as
  ``"timeout"`` after the specified duration and delivers a timeout status
  notification. In subprocess mode the child process is terminated. In thread
  mode the background thread is not force-stopped; callers see the cached timeout
  result immediately while the thread continues until it finishes naturally.

- ``timeout`` (default 1800 s) — subprocess monitor kills the child process after
  this many seconds. Only applies in subprocess mode.

- The parent does not block waiting for subagents. Completion is delivered via the
  ``LOOP_CONTINUE`` hook, which re-enters the parent's loop with a notification
  message.

**4. Child transcript and result delivery**

Subagents always start with a **fresh conversation** — they do not inherit the parent's
message history by default. The result/transcript lifecycle:

- ``context_turns=N`` — the parent's last N turns are prepended to the subagent's
  conversation as context.

- On completion the subagent calls the ``complete`` signal tool with a summary; this
  is queued back to the parent via the ``LOOP_CONTINUE`` hook.

- ``subagent_read_log(agent_id)`` — retrieve the full child transcript from the parent
  after the subagent completes.

- ``subagent_status(agent_id)`` — poll completion/error state without waiting.

.. note::

   The thread-mode transient ``is_runnable`` race (where a tool may briefly appear
   non-runnable while a concurrent subagent thread is active) is a known open issue
   tracked in `gptme#554 <https://github.com/gptme/gptme/issues/554>`_. Since
   `#3072 <https://github.com/gptme/gptme/pull/3072>`_ the crash is non-fatal —
   non-runnable structured tool calls get a paired error ``tool_result`` — but the
   root cause is not yet identified.

Read
----

.. automodule:: gptme.tools.read
    :members:
    :noindex:

Save
----

.. automodule:: gptme.tools.save
    :members:
    :noindex:

Patch
-----

.. automodule:: gptme.tools.patch
    :members:
    :noindex:

Vision
------

.. automodule:: gptme.tools.vision
    :members:
    :noindex:

Screenshot
----------

.. automodule:: gptme.tools.screenshot
    :members:
    :noindex:

Browser
-------

.. automodule:: gptme.tools.browser
    :members:
    :noindex:

Browser FAQ
^^^^^^^^^^^

**Does the browser tool bypass CAPTCHAs?**

No. The Playwright backend is a real browser engine (headless Chromium or Firefox),
so it behaves the same as any headless browser — some CAPTCHAs will block it.
gptme does not currently expose a headed-mode toggle for the built-in Playwright
launcher. To improve success on sites that detect headless Chromium, try Firefox:

.. code-block:: bash

    pipx run playwright==$PW_VERSION install firefox
    export GPTME_BROWSER_ENGINE=firefox

You can also connect to an existing Chromium-compatible browser over Chrome
DevTools Protocol:

.. code-block:: bash

    chromium --remote-debugging-port=9222
    export GPTME_BROWSER_CDP_URL=http://127.0.0.1:9222

**Can I use a full GUI browser with extensions?**

Yes — via the :doc:`howto/computer-use` Docker image, which runs a real Chromium
browser inside a VNC-accessible desktop. Extensions, GUI interaction, and anything
that needs a visible browser window all work there. See the Computer tool and
:doc:`howto/computer-use` for setup details.

**Can I run the browser tool inside Docker?**

The standard Playwright backend works in Docker (headless mode, no display
required). For headed/GUI mode inside Docker, use the computer-use Docker image
which bundles a VNC server and a full desktop environment. See
:doc:`howto/computer-use` for details.

**The page is blocking my scrape — what should I try?**

In order:

1. Switch backends: ``GPTME_BROWSER_ENGINE=firefox`` (different fingerprint than
   Chromium)

2. Connect to an existing Chromium browser:
   ``GPTME_BROWSER_CDP_URL=http://127.0.0.1:9222``

3. Use Anthropic native search (Claude models only):
   ``GPTME_ANTHROPIC_WEB_SEARCH=true``

4. Use the Computer tool with the VNC Docker image for full GUI browser control

Chats
-----

.. automodule:: gptme.tools.chats
    :members:
    :noindex:

Computer
--------

.. include:: computer-use-warning.rst

See :doc:`howto/computer-use` for practical recipes: prerequisites, backend selection,
web vs. native automation, and the observe-act-verify loop.

.. automodule:: gptme.tools.computer
    :members:
    :noindex:

.. _rag:

RAG
---

.. automodule:: gptme.tools.rag
    :members:
    :noindex:

Morph
-----

.. automodule:: gptme.tools.morph
    :members:
    :noindex:

.. _gh:

GH
--

.. automodule:: gptme.tools.gh
    :members:
    :noindex:

Choice
------

.. automodule:: gptme.tools.choice
    :members:
    :noindex:

Elicit
------

.. automodule:: gptme.tools.elicit
    :members:
    :noindex:

Form
----

.. automodule:: gptme.tools.form
    :members:
    :noindex:

Precommit
---------

.. automodule:: gptme.tools.precommit
    :members:
    :noindex:

Autocommit
----------

.. automodule:: gptme.tools.autocommit
    :members:
    :noindex:

Vent
----

.. automodule:: gptme.tools.vent
    :members:
    :noindex:

Complete
--------

.. automodule:: gptme.tools.complete
    :members:
    :noindex:

Restart
-------

.. automodule:: gptme.tools.restart
    :members:
    :noindex:

Lessons
-------

.. automodule:: gptme.tools.lessons
    :members:
    :noindex:

Todo
----

.. automodule:: gptme.tools.todo
    :members:
    :noindex:

MCP
---

The Model Context Protocol (MCP) allows you to extend gptme with custom tools through external servers.
See :doc:`mcp` for configuration and usage details.

.. automodule:: gptme.tools.mcp
    :members:
    :noindex:

.. _tool-allowlist:

Tool Selection & Allowlists
----------------------------

By default gptme loads its full built-in toolset. You can restrict which tools
are active for a given run — either to reduce the agent's surface area or to
build read-only / sandboxed profiles.

Basic usage
^^^^^^^^^^^

Pass a comma-separated list of tool names to ``--tools`` (CLI) or set the
``TOOL_ALLOWLIST`` environment variable:

.. code-block:: bash

    # Exact names — only these tools are loaded
    gptme --tools save,patch,shell,python "refactor this file"

    # Additive: start from defaults and add more
    gptme --tools +rag,browser "research this topic"

    # Subtractive: start from defaults and remove specific tools
    gptme --tools -shell,computer "safer mode"

    # Disable all tools (pure conversation)
    gptme --tools "" "just talk to me"

Glob patterns (``*``, ``?``, ``[...]``) are also supported, matched against tool
names with :func:`fnmatch.fnmatchcase`.

.. _hint-allowlist:

Hint-based patterns
^^^^^^^^^^^^^^^^^^^

Tools can carry **capability hints** — semantic tags that describe what a tool
does. Hint-based allowlist entries let you match entire categories of tools at
once using the ``hint:`` prefix:

.. code-block:: bash

    # Allow only tools annotated as read-only (safe for untrusted workspaces)
    gptme --tools "hint:read-only" "summarise this repo"

    # Mix exact names with hint patterns
    gptme --tools "shell,patch,hint:read-only" "analyse and fix"

The following hints are defined:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Hint
     - Meaning
   * - ``read-only``
     - Tool only reads state; never writes, creates, or deletes.
   * - ``destructive``
     - Tool may modify or delete state. Use with caution in automated runs.
   * - ``idempotent``
     - Tool is safe to call multiple times with the same arguments.
   * - ``closed-world``
     - Tool affects only local state; it does not make network requests or
       reach outside the current environment.

.. note::

    Built-in gptme tools do not carry hints in the current release. Hints are
    currently populated automatically from **MCP tool annotations** (see below).
    You can also set hints explicitly when :doc:`writing custom tools <custom_tool>`.

MCP tool annotations
^^^^^^^^^^^^^^^^^^^^^

When gptme connects to an MCP server, each tool's
`ToolAnnotations <https://modelcontextprotocol.io/docs/concepts/tools#tool-annotations>`_
are mapped to gptme hints:

.. list-table::
   :widths: 40 30 30
   :header-rows: 1

   * - MCP annotation
     - Value
     - gptme hint
   * - ``readOnlyHint``
     - ``true``
     - ``read-only``
   * - ``destructiveHint``
     - ``true`` (and not read-only)
     - ``destructive``
   * - ``idempotentHint``
     - ``true``
     - ``idempotent``
   * - ``openWorldHint``
     - ``false``
     - ``closed-world``

Example MCP server configuration that exposes a read-only filesystem tool:

.. code-block:: json

    {
      "name": "my-tools",
      "description": "My safe read-only tools",
      "tools": [
        {
          "name": "read_file",
          "description": "Read a file from disk",
          "annotations": {
            "readOnlyHint": true,
            "idempotentHint": true
          }
        }
      ]
    }

Once connected, ``gptme --tools "hint:read-only"`` will include ``read_file``
while excluding any MCP tools without the ``read-only`` annotation.

Example profiles
^^^^^^^^^^^^^^^^

**Read-only research agent** — cannot write files or run commands:

.. code-block:: bash

    gptme --tools "browser,rag,chats,hint:read-only" "research X"

**Minimal coding agent** — file editing only, no shell or browser:

.. code-block:: bash

    gptme --tools "read,save,patch,morph,python" "refactor this module"

**Safe MCP integration** — built-in defaults plus only read-only MCP tools:

.. code-block:: bash

    gptme --tools "+hint:read-only" "help me explore this codebase"

**Subagent with restricted tool set** — useful in ``[agent]`` config or when
spawning subagents programmatically:

.. code-block:: toml

    # gptme.toml
    [env]
    TOOL_ALLOWLIST = "shell,patch,save,read,hint:read-only"
