Providers
=========

We support LLMs from several providers, including OpenAI, Anthropic, OpenRouter, Deepseek, Azure, and any OpenAI-compatible server (e.g. ``ollama``, ``llama-cpp-python``).

.. note::

    We are in the process of adding support for configurable `custom providers <custom-providers>`_.

You can find our model recommendations on the :doc:`evals` page.

.. toctree::
   :maxdepth: 2

   custom-providers

To select a provider and model, run ``gptme`` with the ``-m``/``--model`` flag set to ``<provider>/<model>``, for example:

.. code-block:: sh

    gptme "hello" -m openai/gpt-5
    gptme "hello" -m anthropic  # will use provider default
    gptme "hello" -m openrouter/x-ai/grok-4
    gptme "hello" -m deepseek/deepseek-reasoner
    gptme "hello" -m gemini/gemini-2.5-flash
    gptme "hello" -m groq/llama-3.3-70b-versatile
    gptme "hello" -m xai/grok-4
    gptme "hello" -m local/llama3.2:1b

You can list the models known to gptme using ``gptme '/models' - '/exit'``

On first startup API key will be prompted for if no model and no API keys are set in the config/environment. The key will be saved in the configuration file, the provider will be inferred, and its default model used.

Use the ``[env]`` section in the :ref:`global-config` file to store API keys using the same format as the environment variables:

- ``OPENAI_API_KEY="your-api-key"``
- ``ANTHROPIC_API_KEY="your-api-key"``
- ``OPENROUTER_API_KEY="your-api-key"``
- ``GEMINI_API_KEY="your-api-key"``
- ``XAI_API_KEY="your-api-key"``
- ``GROQ_API_KEY="your-api-key"``
- ``DEEPSEEK_API_KEY="your-api-key"``

.. rubric:: OpenAI Subscription

You can use your existing ChatGPT Plus/Pro subscription with gptme. This uses the ChatGPT backend API (Codex endpoint) instead of the OpenAI Platform API, allowing you to leverage your subscription for development.

**Setup:**

Authenticate using the OAuth command (opens browser for login):

.. code-block:: sh

    gptme-auth openai-subscription

This stores credentials locally at ``~/.config/gptme/oauth/openai_subscription.json``.
Access tokens are automatically refreshed before expiry, so you only need to authenticate once.

**Usage:**

.. code-block:: sh

    gptme "hello" -m openai-subscription/gpt-5.2
    gptme "hello" -m openai-subscription/gpt-5.2-codex
    gptme "hello" -m openai-subscription/gpt-5.1

You can also append reasoning levels: ``:low``, ``:medium``, ``:high``, or ``:xhigh``:

.. code-block:: sh

    gptme "solve this problem" -m openai-subscription/gpt-5.2:high

**Available Models:**

- ``gpt-5.2`` - Latest GPT model with reasoning capabilities
- ``gpt-5.2-codex`` - Optimized for code tasks
- ``gpt-5.1-codex-max`` - Maximum capability variant
- ``gpt-5.1-codex`` - Code-optimized
- ``gpt-5.1-codex-mini`` - Smaller code-optimized variant
- ``gpt-5.1`` - Previous generation

.. note::

    This is for **personal development use** with your own ChatGPT Plus/Pro subscription.
    For production or multi-user applications, use the OpenAI Platform API.
    OAuth credentials are stored locally and access tokens are refreshed automatically.

.. rubric:: Local

You can use local LLM models using any OpenAI API-compatible server.

To achieve that with ``ollama``, install it then run:

.. code-block:: sh

    ollama pull llama3.2:1b
    ollama serve
    OPENAI_BASE_URL="http://127.0.0.1:11434/v1" gptme 'hello' -m local/llama3.2:1b

.. note::

    Small models won't work well with tools, severely limiting the usefulness of gptme. You can find an overview of how different models perform on the :doc:`evals` page.
