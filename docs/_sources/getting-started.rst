Getting Started
===============

This guide will help you get started with gptme.

Installation
------------

To install gptme, we recommend using ``pipx``:

.. code-block:: bash

    pipx install gptme

If pipx is not installed, you can install it using pip:

.. code-block:: bash

    pip install --user pipx

.. note::

   Windows is not directly supported, but you can run gptme using WSL or Docker.

Usage
-----

To start your first chat, simply run:

.. code-block:: bash

    gptme

This will start an interactive chat session with the AI assistant.

If you haven't set a :doc:`LLM provider <providers>` API key in the environment or :doc:`configuration <config>`, you will be prompted for one which will be saved in the configuration file.

For detailed usage instructions, see :doc:`usage`.

You can also try the :doc:`examples`.

Quick Examples
--------------

Here are some compelling examples to get you started:

.. code-block:: bash

    # Create applications and games
    gptme 'write a web app to particles.html which shows off an impressive and colorful particle effect using three.js'
    gptme 'create a performant n-body simulation in rust'

    # Work with files and code
    gptme 'summarize this' README.md
    gptme 'refactor this' main.py
    gptme 'what do you see?' image.png  # vision

    # Development workflows
    git status -vv | gptme 'commit'
    make test | gptme 'fix the failing tests'
    gptme 'implement this' https://github.com/gptme/gptme/issues/286

    # Chain multiple tasks
    gptme 'make a change' - 'test it' - 'commit it'

    # Resume conversations
    gptme -r

Next Steps
----------

- Read the :doc:`usage` guide
- Try the :doc:`examples`
- Learn about available :doc:`tools`
- Explore different :doc:`providers`
- Set up the :doc:`server` for web access

Support
-------

For any issues, please visit our `issue tracker <https://github.com/gptme/gptme/issues>`_.
