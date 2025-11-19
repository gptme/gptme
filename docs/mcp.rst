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

    # HTTP MCP Server example
    [[mcp.servers]]
    name = "http-server"
    enabled = true
    url = "https://example.com/mcp"
    headers = { Authorization = "Bearer your-token" }

We also intend to support specifying it in the :ref:`project-config`, and the ability to set it per-conversation.

Management Tool
---------------

gptme includes a powerful MCP management tool that allows you to discover and dynamically load MCP servers during a conversation.

Commands
~~~~~~~~

The ``mcp`` tool provides the following slash-commands:

- ``/search [query]``: Search for MCP servers across registries
- ``/info <server-name>``: Get detailed information about a specific server
- ``/load <server-name>``: Dynamically load an MCP server into the current session
- ``/unload <server-name>``: Unload a previously loaded MCP server
- ``/list``: List all currently configured and loaded MCP servers

Once loaded, the server's tools will be available as ``<server-name>.<tool-name>`` in the conversation.

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

- ``enabled``: Enable/disable MCP support globally
- ``auto_start``: Automatically start MCP servers when needed
- ``servers``: List of MCP server configurations

  - ``name``: Unique identifier for the server
  - ``enabled``: Enable/disable individual server
  - ``command``: Command to start the server (for stdio servers)
  - ``args``: List of command-line arguments (for stdio servers)
  - ``url``: HTTP endpoint URL (for HTTP servers)
  - ``headers``: HTTP headers dictionary (for HTTP servers)
  - ``env``: Environment variables for the server

MCP Server Examples
-------------------

SQLite Server
~~~~~~~~~~~~~

The SQLite server provides database interaction and business intelligence capabilities through SQLite. It enables running SQL queries, analyzing business data, and automatically generating business insight memos:

.. code-block:: toml

    [[mcp.servers]]
    name = "sqlite"
    enabled = true
    command = "uvx"
    args = [
        "mcp-server-sqlite",
        "--db-path",
        "/path/to/sqlitemcp-store.sqlite"
    ]

The server provides these core tools:

Query Tools:

- ``read_query``: Execute SELECT queries to read data
- ``write_query``: Execute INSERT, UPDATE, or DELETE queries
- ``create_table``: Create new tables in the database

Schema Tools:

- ``list_tables``: Get a list of all tables
- ``describe_table``: View schema information for a specific table

Analysis Tools:

- ``append_insight``: Add business insights to the memo resource

Resources:

- ``memo://insights``: A continuously updated business insights memo

The server also includes a demonstration prompt ``mcp-demo`` that guides users through database operations and analysis.

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

Managing MCP Servers
--------------------

gptme provides CLI commands to manage and test your MCP servers:

.. code-block:: bash

    # List all configured MCP servers and check their health
    gptme-util mcp list

    # Test connection to a specific server
    gptme-util mcp test server-name

    # Show detailed information about a server
    gptme-util mcp info server-name

These commands help you verify that your MCP servers are properly configured and accessible.
