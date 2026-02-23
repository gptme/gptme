Configuration
=============

gptme has three configuration files:

- :ref:`global configuration <global-config>`
- :ref:`project configuration <project-config>`
- :ref:`chat configuration <chat-config>`

It also supports :ref:`environment-variables` for configuration, which take precedence over the configuration files.

The CLI also supports a variety of options that can be used to override both configuration values.

.. _global-config:

Global config
-------------

The file is located at ``~/.config/gptme/config.toml``.

Here is an example:

.. code-block:: toml

    [user]
    name = "Erik"
    about = "I am a curious human programmer."
    response_preference = "Don't explain basic concepts"
    avatar = "~/Pictures/avatar.jpg"  # Path to avatar image (or URL)

    [prompt]
    # Additional files to always include in context
    files = ["~/notes/llm-tips.md"]

    # Project descriptions (optional)
    #[prompt.project]
    #myproject = "A description of my project."

    [env]
    # Uncomment to use Claude Sonnet 4.6 by default
    #MODEL = "anthropic/claude-sonnet-4-6"

    # One of these need to be set
    # If none of them are, they will be prompted for on first start
    OPENAI_API_KEY = ""
    ANTHROPIC_API_KEY = ""
    OPENROUTER_API_KEY = ""
    XAI_API_KEY = ""
    GEMINI_API_KEY = ""
    GROQ_API_KEY = ""
    DEEPSEEK_API_KEY = ""

    # Uncomment to use with Ollama
    #MODEL = "local/<model-name>"
    #OPENAI_BASE_URL = "http://localhost:11434/v1"

    # Uncomment to change tool configuration
    #TOOL_FORMAT = "markdown" # Select the tool formal. One of `markdown`, `xml`, `tool`
    #TOOL_ALLOWLIST = "save,append,patch,ipython,shell,browser"  # Comma separated list of allowed tools
    #TOOL_MODULES = "gptme.tools,custom.tools" # List of python comma separated python module path

The ``user`` section configures user identity:

- ``name``: Your display name, shown at the CLI input prompt and as a tooltip on avatar in the web UI (default: ``"User"``).
- ``about``: A description of yourself, included in the system prompt so the assistant knows who it's talking to.
- ``response_preference``: Preferences for how the assistant should respond (e.g. level of detail).
- ``avatar``: Path to your avatar image (supports ``~`` expansion) or URL. Displayed in the web UI next to your messages.

.. note::

    For backward compatibility, ``about_user`` and ``response_preference`` under the ``[prompt]`` section are still supported as fallbacks if not set in ``[user]``.

The ``prompt`` section contains options included in both interactive and non-interactive runs:

- ``files``: A list of additional files to always include in context. Supports absolute paths, ``~`` expansion, and paths relative to the config directory.
- ``project``: A table of project descriptions, keyed by project name, included when working in the matching Git repository.

The ``env`` section contains environment variables that gptme will fall back to if they are not set in the shell environment. This is useful for setting the default model and API keys for :doc:`providers`. It can also be used to set default tool configuration options, see :doc:`custom_tool` for more information.

If you want to configure MCP servers, you can do so in a ``mcp`` section. See :ref:`mcp` for more information.

See :class:`gptme.config.UserConfig` for the API reference.

.. _global-config-local:

Local overrides (``config.local.toml``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can create a ``config.local.toml`` in the same directory (``~/.config/gptme/``) to override or extend values from ``config.toml``. This is useful for keeping secrets (API keys, MCP server credentials) separate from preferences you might commit to your dotfiles.

Example ``config.local.toml``:

.. code-block:: toml

    [env]
    OPENAI_API_KEY = "sk-..."
    ANTHROPIC_API_KEY = "sk-ant-..."

    # Add secret env vars to an MCP server defined in config.toml
    [[mcp.servers]]
    name = "my-server"
    env = { API_KEY = "secret-key" }

Values in ``config.local.toml`` are merged into the main config: dictionary sections are merged recursively, and MCP servers are merged by name (so you can define the server command/args in ``config.toml`` and add secrets in ``config.local.toml``). Scalar values in the local file override the main file.

.. _project-config:

Project config
--------------

The project configuration file is intended to let the user configure how gptme works within a particular project/workspace.

.. note::

    The project configuration file is a very early feature and is likely to change/break in the future.

gptme will look for a ``gptme.toml`` file in the workspace root (this is the working directory if not overridden by the ``--workspace`` option). This file contains project-specific configuration options.

Example ``gptme.toml``:

.. code-block:: toml

    files = ["README.md", "Makefile"]
    prompt = "This is gptme."

This file currently supports a few options:

- ``files``, a list of paths that gptme will always include in the context. If no ``gptme.toml`` is present or if the ``files`` option is unset, gptme will automatically look for common project files, such as: ``README.md``, ``pyproject.toml``, ``package.json``, ``Cargo.toml``, ``Makefile``, ``.cursor/rules/**.mdc``, ``CLAUDE.md``, ``GEMINI.md``.
- ``prompt``, a string that will be included in the system prompt with a ``# Current Project`` header.
- ``base_prompt``, a string that will be used as the base prompt for the project. This will override the global base prompt ("You are gptme v{__version__}, a general-purpose AI assistant powered by LLMs. [...]"). It can be useful to change the identity of the assistant and override some default behaviors.
- ``context_cmd``, a command used to generate context to include when constructing the system prompt. The command will be run in the workspace root and should output a string that will be included in the system prompt. Examples can be ``git status -v`` or ``scripts/context.sh``.

  .. warning::

     The command is executed with shell interpretation. Review ``gptme.toml`` before running gptme in untrusted repositories. See :doc:`security` for details.

- ``rag``, a dictionary to configure the RAG tool. See :ref:`rag` for more information.
- ``plugins``, a dictionary to configure plugins for this project. See :doc:`plugins` for more information. Example:

  .. code-block:: toml

      [plugins]
      paths = ["./plugins", "~/.config/gptme/plugins"]
      enabled = ["my_project_plugin"]

- ``agent``, a dictionary for agent-specific settings. This is primarily used by autonomous agents like gptme-bob. Example:

  .. code-block:: toml

      [agent]
      name = "Bob"
      avatar = "assets/avatar.png"  # Path to avatar image (relative to workspace)

  Options:

  - ``name``: The agent's name, used in system prompts and identification.
  - ``avatar``: Path to an avatar image (relative to workspace) or URL. Used by gptme-webui, gptme-server, and multi-agent UIs to display the agent's profile picture.

- ``env``, a dictionary of environment variables to set for this project. These take precedence over global config but are overridden by shell environment variables.
- ``mcp``, MCP server configuration for this project. See :ref:`mcp` for more information.

See :class:`gptme.config.ProjectConfig` for the API reference.

.. _project-config-local:

Local overrides (``gptme.local.toml``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can create a ``gptme.local.toml`` file next to ``gptme.toml`` to override or extend the project config with values you don't want to commit to version control (e.g. secrets for MCP servers, personal env vars).

The merging behavior is the same as for the :ref:`global local config <global-config-local>`: dictionaries merge recursively, MCP servers merge by name, and scalar values in the local file override the main file.

.. tip::

    Add ``gptme.local.toml`` to your ``.gitignore`` to keep secrets out of version control.


.. _chat-config:

Chat config
-----------

The chat configuration file stores configuration options for a particular chat.
It is used to store the model, toolset, tool format, and streaming/interactive mode.

The chat configuration file is stored as ``config.toml`` in the chat log directory (i.e. ``~/.local/share/gptme/logs/2025-04-23-dancing-happy-walrus/config.toml``). It is automatically generated when a new chat is started and loaded when the chat is resumed, applying any overloaded options passed through the CLI.

See :class:`gptme.config.ChatConfig` for the API reference.

.. _environment-variables:

Environment Variables
---------------------

Besides the configuration files, gptme supports several environment variables to control its behavior:

.. rubric:: Feature Flags

- ``GPTME_CHECK`` - Enable ``pre-commit`` checks (default: true if ``.pre-commit-config.yaml`` present, see :ref:`pre-commit`)
- ``GPTME_CHAT_HISTORY`` - Enable cross-conversation context (default: false)
- ``GPTME_COSTS`` - Enable cost reporting for API calls (default: false)
- ``GPTME_FRESH`` - Enable fresh context mode (default: false)
- ``GPTME_BREAK_ON_TOOLUSE`` - Interrupt generation when tool use occurs in stream (default: true). Set to ``0`` to allow multiple tool calls per LLM response (equivalent to ``--multi-tool`` flag).
- ``GPTME_PATCH_RECOVERY`` - Return file content in error for non-matching patches (default: false)
- ``GPTME_SUGGEST_LLM`` - Enable LLM-powered prompt completion (default: false)

.. rubric:: API Configuration

- ``LLM_API_TIMEOUT`` - Set the timeout in seconds for LLM API requests (default: 600). Must be a valid numeric string (e.g., "600", "1800"). Useful for local LLMs that may take longer to respond.

.. rubric:: Tool Configuration

- ``GPTME_TTS_VOICE`` - Set the voice to use for TTS
- ``GPTME_TTS_SPEED`` - Set the speed to use for TTS (default: 1.0)
- ``GPTME_VOICE_FINISH`` - Wait for TTS speech to finish before exiting (default: false)

.. rubric:: Paths

- ``GPTME_LOGS_HOME`` - Override the default logs folder location

All boolean flags accept "1", "true" (case-insensitive) as truthy values.

.. rubric:: CLI Options via Environment Variables

All CLI options can also be set via environment variables with the ``GPTME_`` prefix.
The variable name is derived from the parameter name in uppercase.

Common examples:

- ``GPTME_MODEL`` - Set the model (equivalent to ``--model``)
- ``GPTME_TOOL_FORMAT`` - Set the tool format (equivalent to ``--tool-format``)
- ``GPTME_WORKSPACE`` - Set the workspace (equivalent to ``--workspace``)
- ``GPTME_TOOL_ALLOWLIST`` - Set allowed tools (equivalent to ``--tools``)

CLI arguments take precedence over environment variables, which take precedence over config file values.

Cross-Conversation Context
~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``GPTME_CHAT_HISTORY=true`` is set, gptme will automatically include summaries from recent conversations in new chat sessions, providing continuity across conversations.

**What it includes:**

- Summaries of the 3 most recent substantial conversations (4+ messages)
- Initial user requests and follow-ups from each conversation
- Last meaningful assistant response from each conversation
- Filters out test conversations and very short interactions

**Benefits:**

- Better continuity for ongoing projects and work
- Understanding of user preferences and communication style
- Context for follow-up questions without manual references
- Awareness of previous technical discussions and solutions

The context is automatically included as a system message when starting new conversations, enabling much better continuity without needing to manually reference previous conversations or maintain persistent notes.
