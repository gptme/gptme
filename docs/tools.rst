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

Chats
-----

.. automodule:: gptme.tools.chats
    :members:
    :noindex:

Computer
--------

.. include:: computer-use-warning.rst

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
