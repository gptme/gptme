Plugin System
=============

The plugin system allows extending gptme with :doc:`custom tools <custom_tool>`, :doc:`hooks <hooks>`, and :ref:`commands <commands>` without modifying the core codebase.

**When to use plugins**: For runtime integration (hooks, custom tools, commands). For lightweight knowledge bundles, see :doc:`lessons` or :doc:`skills` instead.

Plugin Structure
----------------

A plugin is a Python package (directory with ``__init__.py``) that can contain:

.. code-block:: text

   my_plugin/
   ├── __init__.py          # Plugin metadata
   ├── tools/               # Tool modules (optional)
   │   ├── __init__.py     # Makes tools/ a package
   │   └── my_tool.py      # Individual tool modules
   ├── hooks/               # Hook modules (optional)
   │   ├── __init__.py     # Makes hooks/ a package
   │   └── my_hook.py      # Individual hook modules
   └── commands/            # Command modules (optional)
       ├── __init__.py     # Makes commands/ a package
       └── my_command.py   # Individual command modules

Configuration
-------------

Plugins can be configured at two levels:

**User-level** (``~/.config/gptme/config.toml``): Applies to all projects.

**Project-level** (``gptme.toml`` in workspace root): Applies only to this project, merged with user config.

.. code-block:: toml

   [plugins]
   # Paths to search for plugins (supports ~ expansion and relative paths)
   paths = [
       "~/.config/gptme/plugins",
       "~/.local/share/gptme/plugins",
       "./plugins",  # Project-local plugins
   ]

   # Optional: only enable specific plugins (empty = all discovered)
   enabled = ["my_plugin", "another_plugin"]

Project-level plugin paths are relative to the workspace root.

Skills vs Plugins
-----------------

**Choose the right extensibility mechanism**:

+----------------------+------------------+----------------------+
| Need                 | Use              | Why                  |
+======================+==================+======================+
| Share knowledge      | Skills           | Lightweight bundles  |
| and workflows        |                  | (Anthropic format)   |
+----------------------+------------------+----------------------+
| Runtime hooks        | Plugins          | Deep integration     |
| (lifecycle events)   |                  | with gptme runtime   |
+----------------------+------------------+----------------------+
| Custom tools         | Plugins          | Extend capabilities  |
| (new actions)        |                  | via Python code      |
+----------------------+------------------+----------------------+
| Custom commands      | Plugins          | Add CLI commands     |
| (/command)           |                  | for users            |
+----------------------+------------------+----------------------+
| Bundled scripts      | Skills           | Simple file bundles  |
| (no integration)     |                  | without hooks        |
+----------------------+------------------+----------------------+

**Examples**:

- **Skill**: Python best practices guide with example scripts
- **Plugin**: Automatic linting that runs hooks on file save

See :doc:`skills` for lightweight knowledge bundles.

.. _creating-a-plugin:

Creating a Plugin
-----------------

1. Create Plugin Directory Structure
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   mkdir -p ~/.config/gptme/plugins/my_plugin/tools
   touch ~/.config/gptme/plugins/my_plugin/__init__.py
   touch ~/.config/gptme/plugins/my_plugin/tools/__init__.py

2. Create a Tool Module
^^^^^^^^^^^^^^^^^^^^^^^

**my_plugin/tools/hello.py:**

.. code-block:: python

   from gptme.tools.base import ToolSpec

   def hello_world():
       """Say hello to the world."""
       print("Hello from my plugin!")
       return "Hello, World!"

   # Tool specification that gptme will discover
   hello_tool = ToolSpec(
       name="hello",
       desc="Say hello",
       instructions="Use this tool to greet the world.",
       functions=[hello_world],
   )

3. Use Your Plugin
^^^^^^^^^^^^^^^^^^

Start gptme and your plugin tools will be automatically discovered and available:

.. code-block:: bash

   $ gptme "use the hello tool"
   > Using tool: hello
   Hello from my plugin!

How It Works
------------

1. **Discovery**: gptme searches configured plugin paths for directories with ``__init__.py``
2. **Loading**: For each plugin, gptme discovers:

   - Tool modules in ``tools/`` subdirectory
   - Hook modules in ``hooks/`` subdirectory
   - Command modules in ``commands/`` subdirectory

3. **Integration**:

   - Plugin tools are loaded using the same mechanism as built-in tools
   - Plugin hooks are registered during initialization via their ``register()`` functions
   - Plugin commands are registered during initialization via their ``register()`` functions

4. **Availability**:

   - Tools appear in ``--tools`` list and can be used like built-in tools
   - Hooks are automatically triggered at appropriate lifecycle points
   - Commands can be invoked with ``/`` prefix like built-in commands

Plugin Tool Modules
-------------------

Plugins can provide tools in two ways:

Option 1: tools/ as a Package
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create ``tools/__init__.py`` and gptme will import ``my_plugin.tools`` as a package:

.. code-block:: python

   # my_plugin/tools/__init__.py
   from gptme.tools.base import ToolSpec

   tool1 = ToolSpec(...)
   tool2 = ToolSpec(...)

Option 2: Individual Tool Files
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Skip ``tools/__init__.py`` and create individual files:

.. code-block:: text

   my_plugin/tools/
   ├── tool1.py
   └── tool2.py

Each file will be imported as ``my_plugin.tools.tool1``, ``my_plugin.tools.tool2``, etc.

Plugin Hook Modules
-------------------

Plugins can provide hooks to extend gptme's behavior at various lifecycle points, similar to how tools work.

Option 1: hooks/ as a Package
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create ``hooks/__init__.py`` and define a ``register()`` function:

.. code-block:: python

   # my_plugin/hooks/__init__.py
   from gptme.hooks import HookType, register_hook
   from gptme.message import Message

   def my_session_hook(logdir, workspace, initial_msgs):
       """Hook called at session start."""
       yield Message("system", f"Plugin initialized in workspace: {workspace}")

   def register():
       """Register all hooks from this module."""
       register_hook(
           "my_plugin.session_start",
           HookType.SESSION_START,
           my_session_hook,
           priority=0
       )

Option 2: Individual Hook Files
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create individual hook modules without ``hooks/__init__.py``:

.. code-block:: python

   # my_plugin/hooks/logging_hook.py
   from gptme.hooks import HookType, register_hook
   from gptme.message import Message

   def log_tool_execution(log, workspace, tool_use):
       """Log tool executions."""
       print(f"Executing tool: {tool_use.tool}")
       yield  # Hooks must be generators

   def register():
       """Register hooks from this module."""
       register_hook(
           "my_plugin.log_tool",
           HookType.TOOL_PRE_EXECUTE,
           log_tool_execution,
           priority=0
       )

Hook Types
^^^^^^^^^^

Available hook types:

- ``SESSION_START`` - Called at session start
- ``SESSION_END`` - Called at session end
- ``TOOL_PRE_EXECUTE`` - Before tool execution
- ``TOOL_POST_EXECUTE`` - After tool execution
- ``FILE_PRE_SAVE`` - Before saving a file
- ``FILE_POST_SAVE`` - After saving a file
- ``GENERATION_PRE`` - Before generating response
- ``GENERATION_POST`` - After generating response
- And more (see ``gptme.hooks.HookType``)

Hook Registration
^^^^^^^^^^^^^^^^^

Every hook module must have a ``register()`` function that calls ``register_hook()`` for each hook it provides. The plugin system automatically calls ``register()`` during initialization.

.. _plugin-command-modules:

Plugin Command Modules
----------------------

Plugins can provide custom commands that users can invoke with the ``/`` prefix, similar to built-in commands like ``/help`` or ``/exit``.

Option 1: commands/ as a Package
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create ``commands/__init__.py`` and define a ``register()`` function:

.. code-block:: python

   # my_plugin/commands/__init__.py
   from gptme.commands import register_command, CommandContext
   from gptme.message import Message

   def weather_handler(ctx: CommandContext):
       """Handle the /weather command."""
       location = ctx.full_args or "Stockholm"
       # Your weather logic here
       yield Message("system", f"Weather in {location}: Sunny, 20°C")

   def register():
       """Register all commands from this module."""
       register_command("weather", weather_handler, aliases=["w"])

Option 2: Individual Command Files
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create individual command modules without ``commands/__init__.py``:

.. code-block:: python

   # my_plugin/commands/joke.py
   from gptme.commands import register_command, CommandContext
   from gptme.message import Message

   def joke_handler(ctx: CommandContext):
       """Tell a random joke."""
       jokes = [
           "Why did the AI cross the road? To optimize the other side!",
           "What's an AI's favorite snack? Microchips!",
       ]
       import random
       yield Message("system", random.choice(jokes))

   def register():
       """Register command."""
       register_command("joke", joke_handler, aliases=["j"])

Using Plugin Commands
^^^^^^^^^^^^^^^^^^^^^

Once registered, commands can be used like built-in commands:

.. code-block:: bash

   $ gptme
   > /weather London
   Weather in London: Sunny, 20°C

   > /joke
   Why did the AI cross the road? To optimize the other side!

Command Handler Requirements
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Command handlers must:

1. Accept a ``CommandContext`` parameter with:

   - ``args``: List of space-separated arguments
   - ``full_args``: Complete argument string
   - ``manager``: LogManager instance
   - ``confirm``: Confirmation function

2. Be a generator (use ``yield``) that yields ``Message`` objects
3. Be registered via ``register_command()`` in a ``register()`` function

Example: Logging Plugin
-----------------------

A complete example of a plugin that logs tool executions:

.. code-block:: python

   # my_logging_plugin/hooks/tool_logger.py
   from gptme.hooks import HookType, register_hook
   import logging

   logger = logging.getLogger(__name__)

   def log_tool_pre(log, workspace, tool_use):
       """Log before tool execution."""
       logger.info(f"Executing tool: {tool_use.tool} with args: {tool_use.args}")
       yield  # Hooks must be generators

   def log_tool_post(log, workspace, tool_use, result):
       """Log after tool execution."""
       logger.info(f"Tool {tool_use.tool} completed")
       yield

   def register():
       register_hook("tool_logger.pre", HookType.TOOL_PRE_EXECUTE, log_tool_pre)
       register_hook("tool_logger.post", HookType.TOOL_POST_EXECUTE, log_tool_post)

Example: Weather Plugin
-----------------------

A complete example of a weather information plugin:

**my_weather/tools/weather.py:**

.. code-block:: python

   from gptme.tools.base import ToolSpec, ToolUse
   import requests

   def get_weather(location: str) -> str:
       """Get weather for a location."""
       # Implementation
       return f"Weather in {location}: Sunny, 72°F"

   weather_tool = ToolSpec(
       name="weather",
       desc="Get current weather information",
       instructions="Use this tool to get weather for a location.",
       functions=[get_weather],
   )

**Configuration (~/.config/gptme/config.toml):**

.. code-block:: toml

   [plugins]
   paths = ["~/.config/gptme/plugins"]

**Usage:**

.. code-block:: bash

   $ gptme "what's the weather in San Francisco?"
   > Using tool: weather
   Weather in San Francisco: Sunny, 72°F

Distribution
------------

Plugins can be distributed as:

1. **Git repositories**: Clone into plugin directory

   .. code-block:: bash

      git clone https://github.com/user/gptme-plugin ~/.config/gptme/plugins/plugin-name

2. **PyPI packages**: Install and add to plugin path

   .. code-block:: bash

      pip install gptme-weather-plugin
      # Add site-packages location to plugins.paths in gptme.toml

3. **Local directories**: Copy plugin folder to plugin path

   .. code-block:: bash

      cp -r my_plugin ~/.config/gptme/plugins/

Migration from TOOL_MODULES
----------------------------

The plugin system is compatible with the existing ``TOOL_MODULES`` environment variable.

**Old approach:**

.. code-block:: bash

   export TOOL_MODULES="gptme.tools,my_custom_tools"
   gptme

**New approach (gptme.toml):**

.. code-block:: toml

   [plugins]
   paths = ["~/.config/gptme/plugins"]
   enabled = ["my_plugin"]

Both approaches work and can coexist. The plugin system provides better organization and discoverability for complex tool collections.

Future: Hooks and Commands
---------------------------

Future phases will add support for:

- **Hooks**: Plugin-provided hooks for events (e.g., pre-generation, post-execution)
- **Commands**: Plugin-provided commands for the gptme CLI

Stay tuned for updates!

Troubleshooting
---------------

**Plugin not discovered:**

- Ensure plugin directory has ``__init__.py``
- Check plugin path is correctly configured in ``gptme.toml``
- Verify path is absolute or relative to config directory

**Tools not loading:**

- Check ``tools/`` directory exists and has proper structure
- Verify tool modules define ``ToolSpec`` instances
- Look for import errors in gptme logs

**Plugin not enabled:**

- If using ``plugins.enabled`` allowlist, ensure plugin name is included
- Remove ``enabled`` list to load all discovered plugins
