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
    response_preference = "Basic concepts don't need to be explained."
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

    [models]
    # Optional: set a default model (formal alternative to the MODEL env var)
    #default = "anthropic/claude-sonnet-4-6"
    # Optional: curate favorites shown prominently in model pickers (e.g. web UI)
    #favorites = ["anthropic/claude-sonnet-4-6", "openai/gpt-4o", "openrouter/google/gemini-2.0-flash"]

The ``user`` section configures user identity:

- ``name``: Your display name, shown at the CLI input prompt and as a tooltip on avatar in the web UI (default: ``"User"``).
- ``about``: A description of yourself, included in the system prompt so the assistant knows who it's talking to (default: ``"I am a curious human programmer."``).
- ``response_preference``: Preferences for how the assistant should respond (e.g. level of detail, default: ``"Basic concepts don't need to be explained."``).
- ``avatar``: Path to your avatar image (supports ``~`` expansion) or URL. Displayed in the web UI next to your messages.

.. note::

    For backward compatibility, ``about_user`` and ``response_preference`` under the ``[prompt]`` section are still supported as fallbacks if not set in ``[user]``.

The ``prompt`` section contains options included in both interactive and non-interactive runs:

- ``files``: A list of additional files to always include in context. Supports absolute paths, ``~`` expansion, and paths relative to the config directory.
- ``project``: A table of project descriptions, keyed by project name, included when working in the matching Git repository. The default config includes descriptions for ``activitywatch`` and ``gptme`` — when the git root directory name matches one of these keys, the description is automatically injected into the system prompt.

The ``env`` section contains environment variables that gptme will fall back to if they are not set in the shell environment. This is useful for setting the default model and API keys for :doc:`providers`. It can also be used to set default tool configuration options, see :doc:`custom_tool` for more information.

.. _how-model-selection-works:

How model selection works
~~~~~~~~~~~~~~~~~~~~~~~~~

When you start gptme, the model is resolved in this priority order:

1. ``--model`` / ``-m`` CLI flag (highest priority, per-session)
2. Per-chat model saved with ``/model`` — persists across session resumes
3. ``[models].default`` in your global config
4. ``MODEL`` env var (in shell or ``[env]`` section of config)
5. Auto-detection based on which API keys are configured

**Setting a permanent default model:**

The recommended approach is to use ``[models].default`` in your config:

.. code-block:: toml

    [models]
    default = "anthropic/claude-sonnet-4-6"

Alternatively, set ``MODEL`` once in the ``[env]`` section — but do **not** add it twice, since TOML does not allow duplicate keys within the same table.

**Auto-detection when no model is set:**

If no model is configured, gptme will scan your API keys and pick the first available provider in this order:

.. list-table::
   :header-rows: 1

   * - Provider prefix
     - API key env var
   * - ``openai``
     - ``OPENAI_API_KEY``
   * - ``anthropic``
     - ``ANTHROPIC_API_KEY``
   * - ``openrouter``
     - ``OPENROUTER_API_KEY``
   * - ``gemini``
     - ``GEMINI_API_KEY``
   * - ``groq``
     - ``GROQ_API_KEY``
   * - ``xai``
     - ``XAI_API_KEY``
   * - ``deepseek``
     - ``DEEPSEEK_API_KEY``
   * - ``moonshot``
     - ``MOONSHOT_API_KEY``
   * - ``azure``
     - ``AZURE_OPENAI_API_KEY``

So if you have both ``ANTHROPIC_API_KEY`` and ``GROQ_API_KEY`` set, gptme will
use Anthropic (earlier in the list) unless you override with ``MODEL`` or ``--model``.
See :doc:`providers` for the full list and the :doc:`evals` page for model recommendations.

**Using multiple providers:**

You can set API keys for as many providers as you like. Use ``--model`` on the
command line to select a specific model for a session, or set ``[models].default``
in your config to change the permanent default:

.. code-block:: bash

    # Use Groq for a specific session, regardless of default
    gptme "explain this" -m groq/llama-3.3-70b-versatile

    # Use OpenRouter's model picker syntax
    gptme "hello" -m openrouter/google/gemini-2.5-flash

The ``<provider>/<model-name>`` prefix determines which API key is used —
``anthropic/...`` uses ``ANTHROPIC_API_KEY``, ``groq/...`` uses ``GROQ_API_KEY``, etc.

**Using local models (Ollama, llama-cpp-python, etc.):**

Set ``MODEL`` to ``local/<model-name>`` and point ``OPENAI_BASE_URL`` at your
local server. The ``OPENAI_BASE_URL`` setting **only applies** to models with
the ``local/`` prefix — it does not affect OpenAI, Anthropic, or other providers:

.. code-block:: toml

    [env]
    MODEL = "local/llama3.2:3b"
    OPENAI_BASE_URL = "http://localhost:11434/v1"

The ``models`` section configures model selection preferences:

- ``default``: The default chat model, as a fully-qualified model ID (e.g. ``"anthropic/claude-sonnet-4-6"``). A formal alternative to the ``MODEL`` env var; ``models.default`` takes precedence over the ``MODEL`` env var (and ``[env].MODEL`` in the config file), but is itself overridden by an explicit per-chat model or the ``--model`` CLI flag.
- ``favorites``: A list of fully-qualified model IDs (e.g. ``["anthropic/claude-sonnet-4-6", "openai/gpt-4o"]``) curated by the user. These are surfaced prominently in model pickers such as the web UI model selector.

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
- ``GPTME_BREAK_ON_TOOLUSE`` - Interrupt generation when tool use occurs in stream. Default is model-dependent: ``false`` for capable models that support parallel tool calls (e.g. claude-sonnet-4-6, gpt-4o), ``true`` for others. Set to ``0`` to force parallel tool calls, ``1`` to force single tool call per response (equivalent to ``--multi-tool`` flag).
- ``GPTME_PATCH_RECOVERY`` - Return file content in error for non-matching patches (default: false)
- ``GPTME_SUGGEST_LLM`` - Enable LLM-powered prompt completion (default: false)

.. rubric:: API Configuration

- ``LLM_API_TIMEOUT`` - Set the timeout in seconds for LLM API requests (default: 600). Must be a valid numeric string (e.g., "600", "1800"). Useful for local LLMs that may take longer to respond.
- ``GPTME_ANTHROPIC_FAST_MODE`` - Enable Anthropic fast mode for the Anthropic provider (default: false). When enabled, requests set ``speed: "fast"`` for up to ~2.5x higher output tokens/sec at premium pricing — a research preview available on Claude Opus 4.8+. Requires an org with fast-mode access; otherwise the API returns an error. Off by default, so it never affects standard usage. Useful for latency-sensitive callers (e.g. gptme-voice).

.. rubric:: Browser Configuration

- ``GPTME_BROWSER_CDP_URL`` - Connect the Playwright browser backend to an existing Chromium-compatible browser over Chrome DevTools Protocol instead of launching Playwright's bundled Chromium. Example: ``http://127.0.0.1:9222``. Start Chrome/Chromium with ``--remote-debugging-port=9222`` to enable this.

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
