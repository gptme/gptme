Configuration
=============

gptme has two configuration files:

- :ref:`global configuration <global-config>`
- :ref:`project configuration <project-config>`

It also supports environment variables for configuration, which take precedence over the configuration files.

The CLI also supports a variety of options that can be used to override both configuration values and environment variables.

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

The ``env`` section contains environment variables that gptme will fall back to if they are not set in the shell environment. This is useful for setting the default model and API keys for :doc:`providers`.

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

Besides the configuration files, gptme supports several environment variables to control its behavior:

.. rubric:: Feature Flags

- ``GPTME_CHECK`` - Enable precommit checks (default: true if ``.pre-commit-config.yaml`` present)
- ``GPTME_COSTS`` - Enable cost reporting for API calls (default: false)
- ``GPTME_FRESH`` - Enable fresh context mode (default: false)
- ``GPTME_BREAK_ON_TOOLUSE`` - Don't stop generation when tool use occurs in stream (default: true)
- ``GPTME_PATCH_RECOVERY`` - Return file content in error for non-matching patches (default: false)
- ``GPTME_SUGGEST_LLM`` - Enable LLM-powered prompt completion (default: false)

.. rubric:: Tool Configuration

- ``GPTME_TTS_VOICE`` - Set the voice to use for TTS
- ``GPTME_VOICE_FINISH`` - Wait for TTS speech to finish before exiting (default: false)

.. rubric:: Paths

- ``GPTME_LOGS_HOME`` - Override the default logs folder location

All boolean flags accept "1", "true" (case-insensitive) as truthy values.

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

- ``files``, a list of paths that gptme will always include in the context.
- ``prompt``, a string that will be included in the system prompt with a ``# Current Project`` header.
- ``base_prompt``, a string that will be used as the base prompt for the project. This will override the global base prompt ("You are gptme v{__version__}, a general-purpose AI assistant powered by LLMs. [...]"). It can be useful to change the identity of the assistant and override some default behaviors.
- ``rag``, a dictionary to configure the RAG tool. See :ref:`RAG Tool <rag-tool>` for more information.
