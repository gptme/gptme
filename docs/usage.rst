Usage
=====

This guide covers common usage patterns and examples for gptme.

To start a new chat or select an existing one, run:

.. code-block:: bash

    gptme

This will show you a list of past chats, allowing you to select one or start a new one.

To get inspiration for your first prompt, see the :doc:`examples`.

.. contents:: Table of Contents
   :depth: 5
   :local:
   :backlinks: none

Commands
--------

.. TODO: use autodoc from source, like cli reference

You can use these slash-commands for various actions within the chat:

- ``/undo`` - Undo the last action
- ``/log`` - Show the conversation log
- ``/tools`` - Show available tools
- ``/edit`` - Edit the conversation in your editor
- ``/rename`` - Rename the conversation
- ``/fork`` - Create a copy of the conversation
- ``/summarize`` - Summarize the conversation
- ``/replay`` - Re-execute codeblocks in the conversation
- ``/help`` - Show help message
- ``/exit`` - Exit the program

Interfaces
----------

Besides the CLI, gptme can be used through:

- :ref:`server:web ui` - A web-based interface
- :doc:`bot` - GitHub bot integration

For more detailed information about specific features, check out:

- :doc:`tools` - Available tools and their usage
- :doc:`providers` - Supported LLM providers
- :doc:`server` - Web UI and API server setup


Features
--------

.. rubric:: Tools

gptme comes with a variety of tools for different tasks:

- :ref:`tools:shell` - Execute shell commands
- :ref:`tools:python` - Run Python code
- :ref:`tools:browser` - Browse and interact with web content
- :ref:`tools:vision` - Process and analyze images

See the :doc:`tools` page for a complete list of available tools.

Writing Files
^^^^^^^^^^^^^

You can ask the assistant to create new files or modify existing ones:

.. chat::

   User: create a new file named life.py implementing the game of life

   Assistant: Certainly! I'll create a new file named `life.py` implementing Conway's Game of Life. This implementation will use NumPy for efficient array operations and Matplotlib for visualization. Here's the code:
   ```life.py
   import numpy as np
   import matplotlib.pyplot as plt
   from matplotlib.animation import FuncAnimation
   ...
   ```

   System: Saved to life.py

Making Changes
^^^^^^^^^^^^^^

You can start chats and request changes directly from the command line. The contents of any mentioned text files will be included as context, and the assistant will generate patches to apply the requested changes:

.. code-block:: bash

   gptme 'make improvements to life.py so that dead cells fade out over time'

.. chat::

   User: make improvements to life.py so that dead cells fade out over time
   ```life.py
   ...
   ```

   Assistant: To make alive cells green and dead cells black in the Game of Life simulation, we need to modify the colormap and how we represent the cell states. Here's the updated `life.py` script with these changes:
   ```patch life.py
   ...
   ```

   System: Patch applied

Browser Integration
^^^^^^^^^^^^^^^^^^^

With the :ref:`tools:browser` extras installed, the assistant can read URLs (included in the prompt or with its ``read_url`` tool) and "see" web pages by taking screenshots and viewing them.

Multiprompt syntax
^^^^^^^^^^^^^^^^^^

The ``-`` separator allows you to chain multiple prompts together, letting the assistant finish running tools for one prompt before moving on to the next:

.. code-block:: bash

    gptme 'create a project' - 'add tests' - 'commit changes'

This is particularly useful for breaking down complex tasks into steps and creating :doc:`automation` workflows.

Skip confirmation prompts
^^^^^^^^^^^^^^^^^^^^^^^^^

You can skip confirmation prompts using the ``--no-confirm`` flag. This is useful when you are confident the LLM will do what you want, so you don't want to have to confirm actions:

.. code-block:: bash

    gptme --no-confirm 'create a snake game using curses in snake.py, dont run it'

Non-interactive mode
^^^^^^^^^^^^^^^^^^^^

The ``--non-interactive`` flag runs gptme in a mode that terminates after completing all prompts. This is useful for scripting and automation:

.. code-block:: bash

    gptme --non-interactive 'create a snake game using curses in snake.py, dont run it' '-' 'make the snake green and the apple red'

Note: ``--non-interactive`` implies ``--no-confirm``, so you don't need to specify both.

.. _pre-commit:

Pre-commit Integration
^^^^^^^^^^^^^^^^^^^^^^

gptme integrates with `pre-commit <https://pre-commit.com/>`__ to catch code quality issues early. When enabled, it will automatically run pre-commit checks after file modifications, right before yielding control back to the user, to let the assistant address issues without human intervention.

- Enabled automatically if a ``.pre-commit-config.yaml`` exists in any parent directory
- Can be explicitly enabled/disabled with ``GPTME_CHECK=true/false``
- Runs after file modifications to catch issues early
- Shows output in conversation when issues are found
- Distinguishes between automatically fixed issues and those requiring manual fixes

Example workflow:

.. chat::

   User: create a new file named main.py with a simple function

   Assistant: Sure! I'll create a new file named `main.py` with a simple function that adds two numbers together. Here's the code:
   ```save main.py
   def my_function(x, y):
       return x + y
   ```

   System: Saved to main.py

   Assistant: I have created the file.

   System: Pre-commit checks failed
   ```stdout
   typecheck................................................................Failed
   - hook id: typecheck
   - exit code: 1
   main.py: error: Function is missing type hints
   ```

   Assistant: Typechecks failed, I'll update the function to use type hints.
   ```patch main.py
   <<<<<<< ORIGINAL
   def my_function(x, y):
   =======
   def my_function(x: int, y: int) -> int:
   >>>>>>> UPDATED
   ```

   System: Patch applied

   Assistant: Great, the typecheck should now pass.

-----

For further usage examples, see the :doc:`examples` page.
