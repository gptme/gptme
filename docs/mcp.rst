.. _mcp:

MCP
===

gptme acts as a MCP client supporting MCP servers (`Model Context Protocol <https://modelcontextprotocol.io/>`_), allowing integration with external tools and services through a standardized protocol.

We also intend to expose tools in gptme as MCP servers, allowing you to use gptme tools in other MCP clients.

Configuration
-------------

You can configure MCP in your :ref:`global-config` (``~/.config/gptme/config.toml``) file:

.. code-block:: toml

    [mcp]
    enabled = true
    auto_start = true

    [[mcp.servers]]
    name = "my-server"
    enabled = true
    command = "server-command"
    args = ["--arg1", "--arg2"]
    env = { API_KEY = "your-key" }

We also intend to support specifying it in the :ref:`project-config`, and the ability to set it per-conversation.

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

- ``enabled``: Enable/disable MCP support globally
- ``auto_start``: Automatically start MCP servers when needed
- ``servers``: List of MCP server configurations

  - ``name``: Unique identifier for the server
  - ``enabled``: Enable/disable individual server
  - ``command``: Command to start the server
  - ``args``: List of command-line arguments
  - ``env``: Environment variables for the server

MCP Server Examples
-------------------

Memory Server
~~~~~~~~~~~~~

The memory server is a useful example of an MCP server that provides persistent knowledge storage:

.. code-block:: toml

    [[mcp.servers]]
    name = "memory"
    enabled = true
    command = "npx"
    args = [
        "-y",
        "@modelcontextprotocol/server-memory"
    ]
    env = { MEMORY_FILE_PATH = "/path/to/memory.json" }

The memory server provides these tools for knowledge graph manipulation:

- ``create_entities``: Create new entities with observations
- ``create_relations``: Create relationships between entities
- ``add_observations``: Add new observations to entities
- ``read_graph``: Read the entire knowledge graph
- ``search_nodes``: Search for entities and their relations

Running MCP Servers
-------------------

Each server provides its own set of tools that become available to the assistant.

MCP servers can be run in several ways:

- Using package managers like ``npx``, ``uvx``, or ``pipx`` for convenient installation and execution
- Running from source or pre-built binaries
- Using Docker containers

.. warning::
    Be cautious when using MCP servers from unknown sources, as they run with the same privileges as your user.

You can find a list of available MCP servers in the `example servers <https://modelcontextprotocol.io/examples>`_ and MCP directories like `MCP.so <https://mcp.so/>`_.
