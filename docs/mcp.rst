:audience: power-user

.. _mcp:

MCP
===

gptme works as both an MCP **client** (consuming external MCP servers) and an MCP **server** (exposing gptme tools to Claude Desktop, Cursor, and other MCP clients).

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

    # Legacy SSE MCP server example (URL ends in /sse, so SSE is auto-detected)
    [[mcp.servers]]
    name = "sse-server"
    enabled = true
    url = "https://example.com/sse"

HTTP servers use the streamable-HTTP transport by default. A URL whose path ends
in ``/sse`` (the conventional FastMCP SSE endpoint) is connected over the legacy
SSE transport instead. For an SSE endpoint at any other path, set
``transport = "sse"`` explicitly; ``transport`` is ignored for stdio servers.

The same ``[mcp]`` settings are supported in :ref:`project-config`
(``gptme.toml`` in your workspace) and per-conversation configuration.
For example, a project's ``gptme.toml`` can enable its own server:

.. code-block:: toml

    [mcp]
    enabled = true

    [[mcp.servers]]
    name = "project-server"
    command = "server-command"
    args = ["--arg1"]

Servers from all configuration layers are combined. For servers with the same
name, conversation settings take precedence over project settings, which take
precedence over global settings. The winning entry replaces the others whole:
fields from a lower layer's entry are not merged into it. ``gptme-util mcp info``
prints which layer supplied a server (``Source:``) and which layers it
overrides. The ``enabled`` and ``auto_start`` settings follow the same precedence.

Run ``gptme-util mcp list``, ``gptme-util mcp test project-server``, or
``gptme-util mcp info project-server`` from the workspace directory to inspect
and test the merged global and project configuration. Before connecting to a
project-defined server, these commands ask for confirmation and show the target
command or URL (credentials in an HTTP URL — userinfo and query/fragment
parameter values — are redacted). Declining or
supplying no input skips the connection; ``mcp test`` exits unsuccessfully. Connecting to a stdio server runs its configured command,
so approve only project servers you trust.

Management Tool
---------------

gptme includes a powerful MCP management tool that allows you to discover and dynamically load MCP servers during a conversation.

Commands
~~~~~~~~

The ``mcp`` tool provides the following slash-commands:

- ``/mcp search [query]``: Search for MCP servers across registries
- ``/mcp info <server-name>``: Get detailed information about a specific server
- ``/mcp load <server-name>``: Dynamically load an MCP server into the current session
- ``/mcp unload <server-name>``: Unload a previously loaded MCP server
- ``/mcp list``: List all currently configured and loaded MCP servers

It also exposes ``/mcp resources list|read``, ``/mcp templates list``,
``/mcp prompts list|get``, and ``/mcp roots list|add|remove``.

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
  - ``transport``: ``"sse"`` to force the legacy SSE transport for an HTTP server (auto-detected for URLs ending in ``/sse``); otherwise streamable HTTP is used
  - ``env``: Environment variables for the server

OAuth for HTTP servers
~~~~~~~~~~~~~~~~~~~~~~

Some HTTP servers gate access with OAuth instead of a static token. When such a
server answers ``401`` with a ``resource_metadata`` challenge, gptme runs the MCP
authorization-code flow (PKCE) against the authorization server it advertises,
including dynamic client registration and token refresh:

- the authorization URL is printed and opened in a browser when one is available,
- the redirect is captured on a loopback port (``127.0.0.1``), as RFC 8252
  requires for native clients,
- the issued token and client registration are cached per server under
  ``mcp-oauth/`` in the config directory, in a ``0600`` file.

An ``Authorization`` value in ``[mcp.servers.headers]`` always wins over the
flow, so existing static-token setups keep working unchanged. Set
``GPTME_MCP_OAUTH=0`` to disable the flow entirely.

A session that cannot open a browser (server mode, CI) fails with an actionable
error instead of waiting for a redirect: authorize once interactively to fill the
cache, or keep using static headers.

Large tool results
~~~~~~~~~~~~~~~~~~

A tool result longer than 100,000 characters (about 25k tokens) is cut to its head and tail
before it reaches the model. When a conversation directory is active, the full text is saved
under ``tool-outputs/`` and the omission marker names the file. Set
``GPTME_MCP_MAX_OUTPUT_CHARS`` to change the limit.

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

Chrome DevTools Server
~~~~~~~~~~~~~~~~~~~~~~

The `Chrome DevTools MCP server <https://github.com/ChromeDevTools/chrome-devtools-mcp>`_ exposes Chrome DevTools Protocol capabilities — DOM inspection, network traffic analysis, JavaScript console, performance profiling, and Lighthouse audits — directly as MCP tools.

Requires Node.js and ``npx``.

.. code-block:: toml

    [[mcp.servers]]
    name = "chrome-devtools"
    enabled = true
    command = "npx"
    args = ["-y", "chrome-devtools-mcp@latest"]

The server provides tools across several categories:

- **Navigation**: ``navigate_page``, ``new_page``, ``close_page``, ``select_page``
- **Inspection**: ``take_snapshot``, ``take_screenshot``, ``evaluate_script``
- **Network**: ``list_network_requests``, ``get_network_request``
- **Console**: ``list_console_messages``, ``get_console_message``
- **Performance**: ``performance_analyze_insight``, ``lighthouse_audit``
- **Automation**: ``click``, ``type_text``, ``fill_form``, ``hover``, ``drag``

Useful flags:

- ``--headless`` — run without a visible window (server environments)
- ``--isolated`` — clean browser state per session
- ``--slim`` — navigation and screenshots only (lightweight)

For example, to run headless with an isolated browser profile:

.. code-block:: toml

    [[mcp.servers]]
    name = "chrome-devtools"
    enabled = true
    command = "npx"
    args = ["-y", "chrome-devtools-mcp@latest", "--headless", "--isolated"]

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

gptme as an MCP Server
-----------------------

gptme can expose its own tools (shell, Python REPL, file read/save, browser, etc.) as an MCP server so that Claude Desktop, Cursor, and other MCP clients can use them directly.

Quick start for Claude Desktop — add to ``~/.claude/claude_desktop_config.json``:

.. code-block:: json

    {
      "mcpServers": {
        "gptme": {
          "command": "gptme-mcp-server",
          "args": ["--tools", "shell,ipython,save,read"]
        }
      }
    }

Then restart Claude Desktop. The ``shell``, ``ipython``, ``save``, and ``read`` tools will appear in Claude's tool menu.

Running the server manually:

.. code-block:: bash

    # Standalone entrypoint (recommended for Claude Desktop)
    gptme-mcp-server --tools shell,ipython,save,read

    # Or via gptme-util (equivalent)
    gptme-util mcp serve --tools shell,ipython,save,read

Options:

- ``--tools``: Comma-separated tool names to expose. Default: ``shell,ipython,save,append,read``. ``subagent`` and ``mcp`` are always excluded.

- ``--workspace DIR``: Working directory for all tool operations (default: current directory).

- ``--log-level``: Log level sent to stderr (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).

The server is **session-backed**: one persistent gptme session per MCP connection, so the bash shell
retains state, the Python REPL keeps variables, and file operations share a consistent working directory
across multiple tool calls.
