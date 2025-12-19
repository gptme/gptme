.. _acp:

ACP (Agent Client Protocol)
===========================

gptme implements the `Agent Client Protocol (ACP) <https://github.com/ArcadeAI/agent-client-protocol>`_, allowing it to be used as a coding agent from any ACP-compatible editor such as `Zed <https://zed.dev/>`_ and JetBrains IDEs.

This enables a seamless integration where your editor can leverage gptme's powerful toolset (code execution, file editing, web browsing, etc.) directly within your development workflow.

.. note::
   ACP support is currently in development. Phase 1 (basic integration) is complete.
   Future phases will add tool call reporting, session persistence, and enhanced features.

Installation
------------

To use gptme as an ACP agent, install with the ``acp`` extra:

.. code-block:: bash

    pipx install 'gptme[acp]'

Or with pip:

.. code-block:: bash

    pip install 'gptme[acp]'

Usage
-----

Running the Agent
~~~~~~~~~~~~~~~~~

Start the gptme ACP agent:

.. code-block:: bash

    # Via module
    python -m gptme.acp

The agent communicates via stdio using the ACP protocol, making it compatible with any ACP client.

Editor Integration
~~~~~~~~~~~~~~~~~~

**Zed Editor**

Zed has native ACP support. To use gptme as your coding agent:

1. Install gptme with ACP support
2. Configure Zed to use gptme as the agent command
3. The agent will be available in Zed's agent panel

**JetBrains IDEs**

JetBrains IDEs with ACP plugin support can integrate with gptme similarly. Configure the plugin to use ``python -m gptme.acp`` as the agent command.

Architecture
------------

The ACP implementation in gptme consists of:

**GptmeAgent**
    The main agent class implementing the ACP interface. It:

    - Handles ``initialize`` to set up the gptme environment
    - Creates sessions via ``new_session`` with proper logging
    - Processes prompts through gptme's chat infrastructure
    - Streams responses back to the client

**Session Management**
    Each ACP session maps to a gptme conversation with:

    - Isolated log directory
    - Working directory context
    - Full tool access (code execution, file editing, etc.)

Protocol Methods
----------------

The agent implements the following ACP methods:

**initialize**
    Negotiates protocol version and initializes gptme. Called once when a client connects.

**new_session**
    Creates a new gptme session with:

    - Unique session ID
    - Working directory context
    - Initial system prompts and tool configuration

**prompt**
    Handles user prompts by:

    1. Converting ACP content to gptme messages
    2. Running through gptme's chat step
    3. Streaming responses via ``session/update``
    4. Returning completion status

Configuration
-------------

The ACP agent uses gptme's standard configuration. You can customize:

- **Model**: Set via ``GPTME_MODEL`` environment variable or config
- **Tools**: All gptme tools are available by default
- **Working Directory**: Inherited from the ``new_session`` request

Example configuration in ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [general]
    model = "anthropic/claude-sonnet-4-20250514"

    [tools]
    # Tools are auto-confirmed in ACP mode
    # Configure allowlist if needed
    allowlist = ["python", "shell", "patch", "save"]

Capabilities
------------

Through ACP, gptme provides:

- **Code Execution**: Run Python and shell commands
- **File Operations**: Read, write, and patch files
- **Web Browsing**: Search and read web pages
- **Context Awareness**: Workspace and project understanding
- **Conversation Memory**: Persistent session history

Development Roadmap
-------------------

**Phase 1: Basic Integration** ✅ Complete

    - Agent initialization and session creation
    - Prompt handling with response streaming
    - Full tool access through gptme

**Phase 2: Tool Call Reporting** 🚧 In Progress

    - Report tool executions to client
    - Permission request workflow
    - Status lifecycle tracking

**Phase 3: Session Persistence** 🚧 In Progress

    - Save and restore sessions
    - Cancellation support
    - Session metadata management

**Phase 4: Polish & Documentation** 🚧 Current

    - Comprehensive documentation
    - Example configurations
    - Integration guides

See `Issue #977 <https://github.com/gptme/gptme/issues/977>`_ for implementation progress.

Troubleshooting
---------------

**"agent-client-protocol package not installed"**
    Install with: ``pip install 'gptme[acp]'``

**Agent not responding**

    - Check that gptme is properly configured
    - Verify your model API keys are set
    - Check stderr for error messages (ACP uses stdout for protocol)

**Tool execution not working**

    - Ensure tools are not blocked by configuration
    - Check working directory permissions

Related
-------

- :doc:`agents` - Creating custom AI agents with gptme
- :ref:`mcp` - Model Context Protocol integration
- :doc:`config` - gptme configuration options
- :doc:`tools` - Available tools in gptme
