Commands
========

This page documents all available slash commands in gptme.
Commands are entered by typing a forward slash (``/``) followed by the command name.

For CLI arguments and options, see the :doc:`cli` reference.

.. contents:: Table of Contents
   :depth: 3
   :local:
   :backlinks: none

Overview
--------

gptme provides two types of commands:

1. **Built-in commands** - Core commands always available
2. **Tool commands** - Commands registered by enabled tools

To see available commands in your session, use ``/help``.

.. note::
   Some commands are only available when their corresponding tool is enabled.
   Use ``/tools`` to see which tools are currently active.


Built-in Commands
-----------------

Conversation Management
~~~~~~~~~~~~~~~~~~~~~~~

/log
^^^^

Show the conversation log.

.. code-block:: text

   /log           # Show visible messages
   /log --hidden  # Include hidden system messages

/edit
^^^^^

Edit the conversation in your default editor.

Opens the conversation as TOML in ``$EDITOR``, allowing you to modify, delete, or reorder messages.
After saving and closing, the edited conversation is loaded.

.. code-block:: text

   /edit

/undo
^^^^^

Undo the last action(s).

.. code-block:: text

   /undo      # Undo last message
   /undo 3    # Undo last 3 messages

/rename
^^^^^^^

Rename the conversation.

.. code-block:: text

   /rename new-name    # Rename to specific name
   /rename             # Interactive mode, enter empty for auto-generate
   /rename auto        # Auto-generate name using LLM

/fork
^^^^^

Create a copy of the current conversation with a new name.

.. code-block:: text

   /fork my-experiment

/delete
^^^^^^^

Delete a conversation by ID.

**Alias:** ``/rm``

.. code-block:: text

   /delete              # List recent conversations with IDs
   /delete abc123       # Delete conversation with ID abc123
   /delete --force xyz  # Delete without confirmation

.. note::
   Cannot delete the currently active conversation. Start a new conversation first.

/summarize
^^^^^^^^^^

Generate an LLM-powered summary of the conversation.

.. code-block:: text

   /summarize

/replay
^^^^^^^

Replay tool operations from the conversation.

Useful for:

- Re-executing code blocks after making manual changes
- Restoring state (like todo lists) when resuming a conversation
- Debugging tool behavior

.. code-block:: text

   /replay              # Interactive: choose last, all, or tool name
   /replay last         # Replay only the last assistant message with tool uses
   /replay all          # Replay all assistant messages
   /replay todo    # Replay all operations for a specific tool

/export
^^^^^^^

Export the conversation as an HTML file.

.. code-block:: text

   /export                   # Export to <conversation-name>.html
   /export my-chat.html      # Export to specific filename


Model & Token Management
~~~~~~~~~~~~~~~~~~~~~~~~

/model
^^^^^^

List available models or switch to a different model.

**Alias:** ``/models``

.. code-block:: text

   /model                    # Show current model info and list available
   /model openai/gpt-4o      # Switch to specific model
   /model anthropic          # Switch to provider's default model

The model change is persisted to the conversation's config file.

/tokens
^^^^^^^

Show token usage and cost information.

**Alias:** ``/cost``

Displays:

- Session costs (current session usage)
- Conversation costs (all messages)
- Breakdown by input/output tokens

.. code-block:: text

   /tokens

/context
^^^^^^^^

Show detailed context token usage breakdown.

Displays token counts by:

- Role (system, user, assistant)
- Content type (messages, tool uses, thinking blocks)

.. code-block:: text

   /context


Tools & Information
~~~~~~~~~~~~~~~~~~~

/tools
^^^^^^

Manage and inspect tools in the current session.

.. code-block:: text

   /tools              # List loaded tools (with hint about unloaded ones)
   /tools --all        # Show all available tools, including unloaded
   /tools <name>       # Show detailed info for a specific tool
   /tools load <name>  # Load a tool into the current conversation

The ``/tools load`` subcommand lets you add tools mid-conversation without restarting.
When a tool is loaded, its system prompt is injected as a message so the assistant
becomes immediately aware of the new capability.

.. code-block:: text

   /tools load browser    # Enable the browser tool mid-session
   /tools load screenshot # Add screenshot capability

Tab completion is available for tool names.

/doctor
^^^^^^^

Run system diagnostics to check gptme health.

Checks for common issues such as missing dependencies, misconfigured providers,
and tool availability. Run this when something feels off or after a fresh install.

.. code-block:: text

   /doctor

/help
^^^^^

Show the help message with available commands and keyboard shortcuts.

.. code-block:: text

   /help


Providers & Account
~~~~~~~~~~~~~~~~~~~

/account
^^^^^^^^

Show account status and set up API providers.

**Alias:** ``/creds``

Use this to check which providers are configured or run the interactive setup
wizard to add or change a provider (Anthropic, OpenAI, OpenRouter, etc.).

.. code-block:: text

   /account               # Show configured providers and active one
   /account list          # Same as above
   /account setup         # Interactive: choose and configure a provider
   /account setup openrouter    # Set up OpenRouter via OAuth
   /account setup anthropic     # Set up Anthropic with an API key


Session Control
~~~~~~~~~~~~~~~

/exit
^^^^^

Exit gptme, saving the conversation.

.. code-block:: text

   /exit

/restart
^^^^^^^^

Restart the gptme process.

Useful for:

- Applying configuration changes
- Reloading tools after code modifications
- Recovering from state issues

.. code-block:: text

   /restart

/clear
^^^^^^

Clear the terminal screen.

**Alias:** ``/cls``

.. code-block:: text

   /clear


Advanced
~~~~~~~~

/impersonate
^^^^^^^^^^^^

Add a message as if it came from the assistant.

Useful for guiding the conversation or testing tool behavior.

.. code-block:: text

   /impersonate I'll help you with that task.
   /impersonate          # Interactive mode: enter text at prompt

/setup
^^^^^^

Run the gptme setup wizard.

Configures:

- Shell completions (bash, zsh, fish)
- Configuration file
- Project-specific settings

.. code-block:: text

   /setup

/plugin
^^^^^^^

Manage gptme plugins.

.. code-block:: text

   /plugin list           # List discovered plugins
   /plugin info <name>    # Show details about a plugin

/checkpoint
^^^^^^^^^^^

Manage workspace checkpoints for recovery.

Records the current git HEAD as a named checkpoint so you can diff or restore
the workspace to that state later — useful before letting the assistant make
large-scale changes or when experimenting with destructive operations.

Requires a git workspace to be configured for the session (``--workspace``).

.. code-block:: text

   /checkpoint create                  # Record a checkpoint at current HEAD
   /checkpoint create --include-dirty  # Also snapshot uncommitted changes
   /checkpoint list                    # List all recorded checkpoints
   /checkpoint diff <id>               # Diff current state vs a checkpoint
   /checkpoint restore <id>                    # Restore workspace to a checkpoint
   /checkpoint restore <id> --include-dirty    # Restore even with uncommitted changes (discards them)

The ``<id>`` can be a checkpoint number (1-based index) from ``/checkpoint list`` or a HEAD SHA prefix.

/snapshot
^^^^^^^^^

Manage fine-grained workspace snapshots for rollback and agent tree-search.

Records **any** workspace state — committed or dirty — into a side-git shadow
repository.  Snapshots are created automatically before and after each mutating
tool call when the ``auto_snapshots`` plugin is enabled (or
``GPTME_AUTO_SNAPSHOTS=1``), giving agents a full timeline of changes they can
roll back to.

This is **different** from ``/checkpoint``:

- ``/checkpoint`` records a clean git HEAD; requires a committed working tree by
  default.

- ``/snapshot`` records any state (including uncommitted changes) into a
  dedicated shadow repo.  No clean-tree requirement.

Requires a workspace to be configured for the session (``--workspace``).

.. code-block:: text

   /snapshot create [label]     # Record current workspace state (default label: "manual")
   /snapshot list [--limit N]   # List recent snapshots, newest first (default: 20)
   /snapshot restore <sha>      # Roll back workspace to a snapshot
   /snapshot diff <sha>         # Show diff between current workspace and a snapshot

The ``<sha>`` is the short SHA shown by ``/snapshot list``.

**Example — agent tree-search**:

.. code-block:: text

   /snapshot create before-attempt-1
   # ... let the agent make changes ...
   /snapshot list                     # sha: ab1234  before-attempt-1
   /snapshot restore ab1234           # roll back; try a different approach

/backtrack
^^^^^^^^^^

Rewind the **conversation** to a named checkpoint or message index.

Saves named markers in the conversation log so you can undo a bad exchange and
retry with a corrective hint.  Unlike ``/checkpoint`` and ``/snapshot``, this
does **not** touch filesystem state — only the in-memory message log is
rewound.  Use ``/checkpoint`` or ``/snapshot`` if you also need to restore
files.

.. code-block:: text

   /backtrack mark [label]             # Save current position as a checkpoint (auto-label if omitted)
   /backtrack list                     # List saved checkpoints
   /backtrack <label|N>                # Rewind to checkpoint label or message index N
   /backtrack <label|N> --reason TEXT  # Rewind and inject a corrective context message

**Example**:

.. code-block:: text

   /backtrack mark before-tool-run     # save checkpoint
   # ... assistant makes a wrong tool call ...
   /backtrack list                     # shows: cp1  before-tool-run  (index 12)
   /backtrack before-tool-run --reason "The file path was wrong; try /tmp/foo.txt instead"


Tool Commands
-------------

These commands are provided by tools and are only available when the tool is enabled.

/commit (autocommit)
~~~~~~~~~~~~~~~~~~~~

Ask the assistant to review staged changes and create a git commit.

The assistant will:

1. Check ``git status`` and ``git diff --staged``
2. Propose a commit message following Conventional Commits
3. Create the commit (with confirmation)

.. code-block:: text

   /commit

.. note::
   Enable auto-commit on every message by setting ``GPTME_AUTOCOMMIT=true``.

/compact (autocompact)
~~~~~~~~~~~~~~~~~~~~~~

Manually trigger conversation compaction to reduce context size.

.. code-block:: text

   /compact           # Auto-compact using summarization
   /compact auto      # Same as above
   /compact resume    # Generate an LLM-powered resume/summary

.. note::
   Auto-compaction happens automatically when tool outputs exceed size thresholds.

/lesson (lessons)
~~~~~~~~~~~~~~~~~

Manage the lessons system for contextual guidance.

.. code-block:: text

   /lesson                    # Show help
   /lesson list               # List all lessons
   /lesson list tools         # List lessons in a category
   /lesson search <query>     # Search lessons by keyword
   /lesson show <name>        # Show a specific lesson
   /lesson refresh            # Refresh lessons from disk

For more on lessons, see :doc:`lessons`.

/pre-commit (precommit)
~~~~~~~~~~~~~~~~~~~~~~~

Manually run pre-commit checks on the repository.

.. code-block:: text

   /pre-commit

.. note::
   Pre-commit checks run automatically after file modifications when
   a ``.pre-commit-config.yaml`` exists. Control with ``GPTME_CHECK=true/false``.

/mcp (mcp)
~~~~~~~~~~

Manage Model Context Protocol (MCP) servers.

.. code-block:: text

   /mcp search <query>        # Search for MCP servers
   /mcp info <name>           # Show info about a server
   /mcp list                  # List loaded servers
   /mcp load <name>           # Load/start an MCP server
   /mcp unload <name>         # Unload/stop an MCP server

For more on MCP, see :doc:`mcp`.


Tool Shortcuts
--------------

You can execute tool code directly using slash commands with the tool's language tag:

.. code-block:: text

   /sh echo hello             # Execute shell command
   /shell ls -la              # Same as above
   /python print("hello")     # Execute Python code
   /ipython 2 + 2             # Same as above

This is equivalent to writing a code block:

.. code-block:: markdown

   ```shell
   echo hello
   ```


Keyboard Shortcuts
------------------

These shortcuts work in the interactive prompt:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Shortcut
     - Description
   * - ``Ctrl+X Ctrl+E``
     - Edit the current prompt in your editor (``$EDITOR``)
   * - ``Ctrl+J``
     - Insert a newline without executing (for multi-line input)
   * - ``Ctrl+C``
     - Cancel current input or interrupt running operation
   * - ``Ctrl+D``
     - Exit gptme (same as ``/exit``)
   * - ``Tab``
     - Auto-complete commands, paths, and filenames
   * - ``Up/Down``
     - Navigate command history


Command Registration
--------------------

Tools can register custom commands using the ``commands`` parameter in ``ToolSpec``:

.. code-block:: python

   from gptme.tools import ToolSpec
   from gptme.commands import CommandContext

   def my_command(ctx: CommandContext):
       ctx.manager.undo(1, quiet=True)  # Remove the command from log
       print(f"Arguments: {ctx.args}")
       # Optionally yield Message objects

   tool = ToolSpec(
       name="mytool",
       desc="My custom tool",
       commands={
           "mycommand": my_command,
       },
   )

See :doc:`custom_tool` for more on creating tools with commands.
