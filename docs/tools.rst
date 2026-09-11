:audience: user

Tools
=====

gptme's tools enable AI agents to execute code, edit files, browse the web, process images, and interact with your computer.

Overview
--------

Each tool has its own reference page, listed here by category.

📁 File System
~~~~~~~~~~~~~~

- :doc:`tools/read` - Read files in any format
- :doc:`tools/save` - Create and overwrite files
- :doc:`tools/patch` - Apply precise changes to existing files
- :doc:`tools/morph` - Apply fast targeted edits using Morph Fast Apply
- :doc:`tools/hashline-edit` - Snapshot-anchored line-range edits with stale-file detection

💻 Code & Development
~~~~~~~~~~~~~~~~~~~~~

- :doc:`tools/python` - Execute Python code interactively with full library access
- :doc:`tools/shell` - Run shell commands and manage system processes
- :doc:`tools/gh` - Interact with GitHub issues, PRs, and repositories
- :doc:`tools/precommit` - Automatically run pre-commit checks after file saves
- :doc:`tools/autocommit` - Automatically prompt for git commits after file modifications

🌐 Web & Research
~~~~~~~~~~~~~~~~~

- :doc:`tools/browser` - Browse websites, take screenshots, and read web content
- :doc:`tools/rag` - Index and search through documentation and codebases
- :doc:`tools/chats` - Search past conversations for context and references

👁️ Visual & Interactive
~~~~~~~~~~~~~~~~~~~~~~~

- :doc:`tools/vision` - Analyze images, diagrams, and visual content
- :doc:`tools/screenshot` - Capture your screen for visual context
- :doc:`tools/computer` - Control desktop applications through visual interface

🤝 User Interaction
~~~~~~~~~~~~~~~~~~~

- :doc:`tools/choice` - Present multiple-choice options to the user
- :doc:`tools/elicit` - Request structured single-field input from the user
- :doc:`tools/form` - Present a multi-field form for structured user input

⚡ Advanced Workflows
~~~~~~~~~~~~~~~~~~~~~

- :doc:`tools/tmux` - Manage long-running processes in terminal sessions
- :doc:`tools/subagent` - Delegate subtasks to specialized agent instances
- :doc:`tools/complete` - Signal that the autonomous session is finished
- :doc:`tools/restart` - Restart the gptme process after configuration changes
- :doc:`tools/vent` - Emit in-the-moment friction signals to a durable ledger
- :doc:`tools/request-tool-change` - Record a structured request for a different tool configuration (opt-in, audit-only)

🧠 Knowledge & Planning
~~~~~~~~~~~~~~~~~~~~~~~

- :doc:`tools/lessons` - Access contextual lessons and behavioral guidance
- :doc:`tools/todo` - Manage a conversation-scoped working memory task list

🔌 Extensions
~~~~~~~~~~~~~

- :doc:`tools/mcp` - Discover and connect Model Context Protocol servers


.. toctree::
   :hidden:

   tools/read
   tools/save
   tools/patch
   tools/morph
   tools/hashline-edit
   tools/python
   tools/shell
   GitHub (gh) <tools/gh>
   tools/precommit
   tools/autocommit
   tools/browser
   tools/rag
   tools/chats
   tools/vision
   tools/screenshot
   tools/computer
   tools/choice
   tools/elicit
   tools/form
   tools/tmux
   tools/subagent
   tools/complete
   tools/restart
   tools/vent
   Request Tool Change <tools/request-tool-change>
   tools/lessons
   tools/todo
   tools/mcp

Tool Interface Architecture
---------------------------

gptme's default tool interface is **Programmatic Tool Calling (PTC)**: the model
writes executable code in fenced code blocks, and gptme runs it directly. For the
default ``markdown`` and ``xml`` formats, no JSON schemas are sent to the model
and no JSON-structured responses are parsed.

The primary dispatch path is ``"markdown"`` format: a fenced code block whose
language tag identifies any registered tool name (``python``, ``shell``, ``save``,
``patch``, and others), and whose content gptme routes to
``ToolSpec.execute(code, args, kwargs)``. For Python, this means IPython's
``run_cell()``; for Shell, ``subprocess.Popen`` with a stateful bash shell.

gptme also supports a **provider-native tool mode** (``"tool"`` format) for
OpenAI and Anthropic APIs, where ``ToolSpec`` parameters are converted to
JSON-schema definitions and sent to the provider — trading context-rot resilience
for provider-side tool routing compatibility.

**Why default to PTC?** A 2026 benchmark study (arXiv:2608.06370, *"The Bitter Lesson of
Tool Calling"*) found that PTC matches or exceeds JSON-schema tool calling on
11/14 models and — critically — **maintains accuracy under context rot** (long
sessions with accumulated tool history) while JSON-schema accuracy degrades ~2.3%.
Autonomous gptme sessions routinely accumulate 50–200 tool calls; this is exactly
the regime where JSON-schema approaches falter.

See :doc:`design/ptc-tool-interface` for the full architecture documentation and
2026-08-13 audit of dispatch paths in ``gptme/tools/``.

Combinations
------------

The real power emerges when tools work together:

- **Web Research + Code**: :doc:`tools/browser` + :doc:`tools/python` - Browse documentation and implement solutions
- **Visual Development**: :doc:`tools/vision` + :doc:`tools/patch` - Analyze UI mockups and update code accordingly
- **System Automation**: :doc:`tools/shell` + :doc:`tools/python` - Combine system commands with data processing
- **Interactive Debugging**: :doc:`tools/screenshot` + :doc:`tools/computer` - Visual debugging and interface automation
- **Knowledge-Driven Development**: :doc:`tools/rag` + :doc:`tools/chats` - Learn from documentation and past conversations

.. _tool-allowlist:

Tool Selection & Allowlists
----------------------------

By default gptme loads its full built-in toolset. You can restrict which tools
are active for a given run — either to reduce the agent's surface area or to
build read-only / sandboxed profiles.

Basic usage
~~~~~~~~~~~

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

    # Strict audit mode: only the built-in read tool, no writes or execution
    gptme --tools read-only "summarise this repo"

Glob patterns (``*``, ``?``, ``[...]``) are also supported, matched against tool
names with :func:`fnmatch.fnmatchcase`.

``read-only`` is a named preset, not a hint pattern. It expands to the built-in
``read`` tool only, and cannot be combined with other tool names. This makes it
safe for auditing untrusted workspaces where ``shell``, ``ipython``, ``save``,
``append`` and ``patch`` must stay unavailable. Use ``hint:read-only`` only when
you explicitly want to trust third-party tool annotations, such as MCP server
metadata.

.. _hint-allowlist:

Hint-based patterns
~~~~~~~~~~~~~~~~~~~

Tools can carry **capability hints** — semantic tags that describe what a tool
does. Hint-based allowlist entries let you match entire categories of tools at
once using the ``hint:`` prefix:

.. code-block:: bash

    # Allow only tools annotated as read-only
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

    The built-in ``read`` tool carries the ``read-only`` hint. MCP tools can also
    carry the hint through server-supplied annotations (see below), so
    ``hint:read-only`` is broader than the strict ``read-only`` preset.

MCP tool annotations
~~~~~~~~~~~~~~~~~~~~~

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
~~~~~~~~~~~~~~~~

These are ad-hoc allowlists; for gptme's built-in agent profiles (``explorer``,
``researcher``, ``developer``, ``verifier``), see :doc:`profiles`.

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
