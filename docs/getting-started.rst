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

.. tip::

   Some gptme tools require additional system dependencies (playwright, tmux, gh, etc.).
   For extras, source installation, and system dependencies, see :doc:`system-dependencies`.

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

Local Models (No API Key Required)
-----------------------------------

To run gptme without an API key, use a local model via `Ollama <https://ollama.com>`_:

.. code-block:: bash

    # Install Ollama (see https://ollama.com), then pull a model
    ollama pull llama3.2:1b
    ollama serve  # run in background or separate terminal

    # Use with gptme (OPENAI_BASE_URL is required by the local provider)
    export OPENAI_BASE_URL="http://127.0.0.1:11434/v1"
    gptme "hello" -m local/llama3.2:1b

For better results on coding tasks, use a larger model:

.. code-block:: bash

    ollama pull llama3.1:8b
    export OPENAI_BASE_URL="http://127.0.0.1:11434/v1"
    gptme -m local/llama3.1:8b

.. tip::

   Local models work well for simple tasks and private workflows. For complex multi-step
   coding work, API-based models (Claude, GPT-4o) give better results.

   If gptme shows an error about the summary model, configure ``model.summary`` in
   :doc:`config` to point to a local model, or pass ``-m local/MODEL_NAME`` to use the
   same model for both chat and summaries.

See :doc:`providers` for ``llama.cpp``, Groq, and all other local and remote provider options.


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
