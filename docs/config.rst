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

    [prompt]
    about_user = "I am a curious human programmer."
    response_preference = "Don't explain basic concepts"

    [env]
    # Uncomment to use Claude 3.5 Sonnet by default
    #MODEL = "anthropic/claude-3-5-sonnet-20240620"

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

The ``prompt`` section contains options for the prompt.

The ``env`` section contains environment variables that gptme will fall back to if they are not set in the shell environment. This is useful for setting the default model and API keys for :doc:`providers`. It can also be used to set default tool configuration options, see :doc:`custom_tool` for more information.

If you want to configure MCP servers, you can do so in a ``mcp`` section. See :ref:`mcp` for more information.

See :class:`gptme.config.UserConfig` for the API reference.

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

- ``env``, a dictionary of environment variables to set for this project. These take precedence over global config but are overridden by shell environment variables.
- ``mcp``, MCP server configuration for this project. See :ref:`mcp` for more information.

See :class:`gptme.config.ProjectConfig` for the API reference.


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

.. rubric:: Deprecated Environment Variables

- ``GPTME_TOOLUSE_PARALLEL`` - **DEPRECATED**: Previously enabled parallel thread execution of tool calls, but caused thread-safety issues with prompt_toolkit. Use ``GPTME_BREAK_ON_TOOLUSE=0`` instead for multi-tool mode with sequential execution.

.. rubric:: API Configuration

- ``LLM_API_TIMEOUT`` - Set the timeout in seconds for LLM API requests (default: 600). Must be a valid numeric string (e.g., "600", "1800"). Useful for local LLMs that may take longer to respond.

.. rubric:: Tool Configuration

- ``GPTME_TTS_VOICE`` - Set the voice to use for TTS
- ``GPTME_TTS_SPEED`` - Set the speed to use for TTS (default: 1.0)
- ``GPTME_VOICE_FINISH`` - Wait for TTS speech to finish before exiting (default: false)

.. rubric:: Paths

- ``GPTME_LOGS_HOME`` - Override the default logs folder location

All boolean flags accept "1", "true" (case-insensitive) as truthy values.

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
