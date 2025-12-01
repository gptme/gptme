Getting Started
===============

This guide will help you get started with gptme.

Installation
------------

To install gptme, we recommend using ``pipx`` or ``uv``:

.. code-block:: bash

    pipx install gptme
    # or
    uv tool install gptme

If pipx is not installed, you can install it using pip:

.. code-block:: bash

    pip install --user pipx

If ``uv`` is not installed, you can install it using pip, pipx, or your system package manager.

.. note::

   Windows is not directly supported, but you can run gptme using WSL or Docker.

Optional System Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some gptme features require additional non-Python dependencies. These are optional and only needed for specific tools:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Dependency
     - Purpose
     - Installation
   * - ``playwright``
     - Browser automation for the browser tool
     - ``pipx inject gptme playwright && playwright install``
   * - ``lynx``
     - Text-based web browser (alternative to playwright)
     - ``apt install lynx`` (Debian/Ubuntu) or ``brew install lynx`` (macOS)
   * - ``tmux``
     - Terminal multiplexer for long-running commands
     - ``apt install tmux`` (Debian/Ubuntu) or ``brew install tmux`` (macOS)
   * - ``gh``
     - GitHub CLI for the gh tool
     - See `GitHub CLI installation <https://cli.github.com/>`_
   * - ``wl-clipboard``
     - Wayland clipboard support
     - ``apt install wl-clipboard`` (Debian/Ubuntu)
   * - ``pdftotext``
     - PDF text extraction
     - ``apt install poppler-utils`` (Debian/Ubuntu) or ``brew install poppler`` (macOS)

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