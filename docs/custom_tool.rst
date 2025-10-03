Custom Tools
============

Introduction
------------
There are three main approaches to extending gptme's functionality:

1. **Custom Tools**: Native gptme tools that integrate deeply with the assistant.
2. **Script Tools**: Standalone scripts that can be called via the shell tool.
3. **MCP Tools**: Tools that communicate via the Model Context Protocol, allowing language-agnostic tools that can be shared between different LLM clients.

This guide primarily covers the first two approaches. For information about MCP tools, see :doc:`mcp`.

Script-based Tools
------------------

The simplest way to extend gptme is by writing standalone scripts. These can be:

- Written in any language
- Run independently of gptme
- Called via the shell tool
- Easily tested and maintained

Benefits of script-based tools:

- Simple to create and maintain
- Can be run and tested independently
- No gptme dependency
- Flexible language choice
- Isolated dependencies

Limitations:

- Requires shell tool access
- Can't attach files/images to messages
- Not listed in tools section
- No built-in argument validation

For script-based tools, no registration is needed. Simply include them in the gptme context to make the agent aware of them.

1. Place scripts in a ``tools/`` directory (or any other location)
2. Make them executable (``chmod +x tools/script.py``)
3. Use via the shell tool (``gptme 'test our new tool' tools/script.py``)

Creating a Custom Tool
----------------------

When you need deeper integration with gptme, you can create a custom tool by defining a new instance of the ``ToolSpec`` class.

Custom tools are necessary when you need to:

- Attach files/images to messages
- Get included in the tools section
- Use without shell tool access
- Validate arguments
- Handle complex interactions

The ``ToolSpec`` class requires these parameters:

- **name**: The name of the tool.
- **desc**: A description of what the tool does.
- **instructions**: Instructions on how to use the tool.
- **examples**: Example usage of the tool.
- **execute**: A function that defines the tool's behavior when executed.
- **block_types**: The block types to detects.
- **parameters**: A list of parameters that the tool accepts.

Examples
--------

For examples of script-based tools, see:

**gptme-contrib** - A collection of community-contributed tools and scripts:

- `Twitter CLI <https://github.com/gptme/gptme-contrib/blob/master/scripts/twitter.py>`_: Twitter client with OAuth support
- `Perplexity CLI <https://github.com/gptme/gptme-contrib/blob/master/scripts/perplexity.py>`_: Perplexity search tool

**Standalone Tools** - Independent tool repositories:

- `gptme-rag <https://github.com/gptme/gptme-rag/>`_: Document indexing and retrieval

For examples of custom tools, see:

- `Screenshot tool <https://github.com/gptme/gptme/blob/master/gptme/tools/screenshot.py>`_: Takes screenshots
- `Browser tool <https://github.com/gptme/gptme/blob/master/gptme/tools/browser.py>`_: Web browsing and screenshots
- `Vision tool <https://github.com/gptme/gptme/blob/master/gptme/tools/vision.py>`_: Image viewing and analysis

Basic Custom Tool Example
~~~~~~~~~~~~~~~~~~~~~~~~~

Here's a minimal example of a custom tool:

.. code-block:: python

    from gptme.tools import ToolSpec, Parameter, ToolUse
    from gptme.message import Message

    def execute(code, args, kwargs, confirm):
        name = kwargs.get('name', 'World')
        yield Message('system', f"Hello, {name}!")

    tool = ToolSpec(
        name="hello",
        desc="A simple greeting tool",
        instructions="Greets the user by name",
        execute=execute,
        block_types=["hello"],
        parameters=[
            Parameter(
                name="name",
                type="string",
                description="Name to greet",
                required=False,
            ),
        ],
    )

Command Registration
--------------------

In addition to defining tools, you can register custom commands that users can invoke with ``/command`` syntax.

Registering Commands in Tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tools can register commands in their ``ToolSpec`` definition:

.. code-block:: python

   from gptme.tools.base import ToolSpec
   from gptme.commands import CommandContext
   from gptme.message import Message

   def handle_my_command(ctx: CommandContext) -> Generator[Message, None, None]:
       """Handle the /my-command."""
       ctx.manager.undo(1, quiet=True)  # Remove command message
       yield Message("system", "Command executed!")

   tool = ToolSpec(
       name="my_tool",
       desc="Tool with custom command",
       commands={
           "my-command": handle_my_command,
       }
   )

Command Examples
~~~~~~~~~~~~~~~~

**Commit Command (autocommit tool):**

.. code-block:: python

   def handle_commit_command(ctx: CommandContext) -> Generator[Message, None, None]:
       """Handle the /commit command."""
       ctx.manager.undo(1, quiet=True)
       from ..util.context import autocommit
       yield autocommit()

   tool = ToolSpec(
       name="autocommit",
       commands={"commit": handle_commit_command}
   )

**Pre-commit Command (precommit tool):**

.. code-block:: python

   def handle_precommit_command(ctx: CommandContext) -> Generator[Message, None, None]:
       """Handle the /pre-commit command."""
       ctx.manager.undo(1, quiet=True)
       from ..util.context import run_precommit_checks
       success, message = run_precommit_checks()
       if not success and message:
           yield Message("system", message)

   tool = ToolSpec(
       name="precommit",
       commands={"pre-commit": handle_precommit_command}
   )

Command Context
~~~~~~~~~~~~~~~

Command handlers receive a ``CommandContext`` with:

- ``args``: List of command arguments
- ``full_args``: Full argument string
- ``manager``: LogManager for accessing conversation
- ``confirm``: Function for user confirmation

Command Best Practices
~~~~~~~~~~~~~~~~~~~~~~

1. **Undo command message**: Always call ``ctx.manager.undo(1, quiet=True)`` to remove the command from log
2. **Yield Messages**: Return system messages to provide feedback
3. **Handle errors**: Use try-except to handle failures gracefully
4. **Document commands**: Mention commands in tool's ``instructions`` field

Choosing an Approach
--------------------
Use **script-based tools** when you need:

- Standalone functionality
- Independent testing/development
- Language/framework flexibility
- Isolated dependencies

Use **custom tools** when you need:

- File/image attachments
- Tool listing in system prompt
- Complex argument validation
- Operation without shell access

Registering the Tool
--------------------
To ensure your tool is available for use, you can specify the module in the ``TOOL_MODULES`` env variable or
setting in your :doc:`project configuration file <config>`, which will automatically load your custom tools.

.. code-block:: toml

    [env]
    TOOL_MODULES = "gptme.tools,yourpackage.your_custom_tool_module"

Don't remove the ``gptme.tools`` package unless you know exactly what you are doing.

Ensure your module is in the Python path by either installing it
(e.g. with ``pip install .`` or ``pipx runpip gptme install .``, depending on installation method)
or by temporarily modifying the `PYTHONPATH` environment variable. For example:

.. code-block:: bash

    export PYTHONPATH=$PYTHONPATH:/path/to/your/module

This lets Python locate your module during development and testing without requiring installation.

Community Tools
---------------
The `gptme-contrib <https://github.com/gptme/gptme-contrib>`_ repository provides a collection of community-contributed tools and scripts.
This makes it easier to:

- Share tools between agents
- Maintain consistent quality
- Learn from examples
- Contribute your own tools

To use these tools, you can either:

1. Clone the repository and use the scripts directly
2. Copy specific scripts to your local workspace
3. Fork the repository to create your own collection
