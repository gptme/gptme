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
