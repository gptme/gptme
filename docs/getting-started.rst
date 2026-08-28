Getting Started
===============

This guide will help you get started with gptme.

Installation
------------

The quickest way to install gptme is with the one-line installer:

.. code-block:: bash

    curl -sSf https://gptme.ai/install.sh | sh

This auto-detects ``uv`` or ``pipx`` and installs gptme with browser support.
Pass ``--help`` for options (``--dev``, ``--extras``, ``--no-extras``, ``--yes``).

Alternatively, install directly with ``pipx`` or ``uv``:

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

.. _local-models:

Local Models (No API Key Required)
-----------------------------------

To run gptme without an API key, use a local model via `Ollama <https://ollama.com>`_:

.. code-block:: bash

    # Install Ollama (see https://ollama.com), then pull a model
    ollama pull llama3.2:1b
    ollama serve  # run in background or separate terminal

    # Check that gptme can see the local server (probes /v1/models)
    gptme providers list

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

See :doc:`providers` for Groq and all other built-in provider options, or :doc:`providers-custom`
for Ollama, vLLM, and custom server setup.

Free Cloud Providers (No Credit Card Required)
----------------------------------------------

Several cloud providers offer free tiers that work with gptme out of the box.

**OpenRouter** (recommended: one browser sign-in, no credit card)

OpenRouter aggregates free model tiers behind one API key. The ``:free`` catalog
**rotates** — last month's model id is often gone. Prefer the free router, and
pin a specific ``:free`` model only after you have confirmed it is still listed.

On a fresh install, start ``gptme`` and choose **OpenRouter** in the startup
provider setup. gptme opens the browser OAuth flow before entering the chat.
On an already configured installation, you can instead run
``/account setup openrouter`` inside a session.

Then use the default free router:

.. code-block:: bash

    # OpenRouter model id is openrouter/free
    gptme "hello" -m openrouter/openrouter/free

    # Pin a currently listed free model (catalog rotates)
    gptme "hello" -m openrouter/cohere/north-mini-code:free

The doubled ``openrouter/openrouter/free`` is intentional: gptme's
``<provider>/<model>`` split plus OpenRouter's own ``openrouter/free`` model id.
``-m openrouter/free`` sends ``model=free`` and 404s.

Verified 2026-08-28 (chat + gptme ``shell`` tool, shared free pool):

- ``openrouter/openrouter/free`` — 200k ctx, tools work. Recommended default.
- ``openrouter/cohere/north-mini-code:free`` — 256k ctx, tools work.

Shared-pool ``:free`` endpoints 429 often (Gemma 4 and GLM-5.2 did during the
same probe). Retry, pick another listed model, or add your own provider key at
https://openrouter.ai/settings/integrations. Omitting ``:free`` routes to paid
inference. Current catalog: https://openrouter.ai/models?max_price=0

**Google Gemini** (generous free quota, 1 M token context)

.. code-block:: bash

    # Get a free API key at https://aistudio.google.com/apikey (no credit card)
    export GEMINI_API_KEY="your-key"
    gptme "hello" -m gemini/gemini-2.5-flash

**Groq** (fast inference, free tier)

.. code-block:: bash

    # Get a free API key at https://console.groq.com (no credit card)
    export GROQ_API_KEY="your-key"
    gptme "hello" -m groq/llama-3.3-70b-versatile

.. tip::

   Free cloud tiers give better results than local small models for most tasks, while still
   requiring zero spend. OpenRouter's ``/account setup`` is the fastest path — one browser
   sign-in configures everything.

   For truly private workflows, prefer :ref:`local models <local-models>` or a self-hosted
   server. Cloud providers receive your prompts on their infrastructure.


Next Steps
----------

- Match your task to a safe execution surface with :doc:`howto/choose-workflow`
- Read the :doc:`usage` guide
- Try the :doc:`examples`
- Learn about available :doc:`tools`
- Explore different :doc:`providers`
- Set up the :doc:`server` for web access

Support
-------

For any issues, please visit our `issue tracker <https://github.com/gptme/gptme/issues>`_.
