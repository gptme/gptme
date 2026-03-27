Providers
=========

We support LLMs from several providers, including OpenAI, Anthropic, OpenRouter, Deepseek, Azure, and any OpenAI-compatible server (e.g. ``ollama``, ``llama-cpp-python``).

.. note::

    We are in the process of adding support for configurable `custom providers <custom-providers>`_.

.. rubric:: Provider Plugins (Entry Points)

Third-party packages can register LLM providers via Python entry points, making them available immediately after ``pip install`` without any configuration changes.

**How it works:** A plugin package declares an entry point in the ``gptme.providers`` group::

    [project.entry-points."gptme.providers"]
    minimax = "gptme_provider_minimax:provider"

Where ``provider`` is a ``ProviderPlugin`` instance.

**Usage:** Once installed, use the provider name as the model prefix::

    pip install gptme-provider-minimax
    gptme "hello" -m minimax/abab6.5s-chat

**Creating a provider plugin:**

.. code-block:: python

    from gptme.llm.models import ModelMeta, ProviderPlugin

    provider = ProviderPlugin(
        name="minimax",                          # Unique provider name
        api_key_env="MINIMAX_API_KEY",           # Env var for API key
        base_url="https://api.minimax.chat/v1",  # OpenAI-compatible endpoint
        models=[
            ModelMeta(provider="unknown", model="minimax/abab6.5s-chat", context=245_760),
        ],
    )

**ProviderPlugin fields:**

================= ======== ==========================================================
Field             Required Description
================= ======== ==========================================================
``name``           Yes      Unique provider name (e.g. ``"minimax"``)
``api_key_env``    Yes      Environment variable holding the API key
``base_url``       Yes      OpenAI-compatible API base URL
``models``         No       List of ``ModelMeta`` objects
``init``           No       Custom ``(Config) -> None``; ``None`` = auto-init OpenAI client
================= ======== ==========================================================

If ``init`` is provided, it **must** register an OpenAI-compatible client before returning, or gptme will raise a ``RuntimeError``.

Plugin providers are auto-initialised on first use and routed through the OpenAI client path.

.. note::

   For new plugins, consider using the :ref:`unified plugin system <unified-plugins>` (``gptme.plugins`` entry-point group) instead. It lets a single package provide tools, hooks, commands, **and** a provider together. The ``gptme.providers`` group still works and is supported for backward compatibility.

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
    gptme "hello" -m gptme/claude-sonnet-4-6

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

    gptme "hello" -m openai-subscription/gpt-5.4
    gptme "hello" -m openai-subscription/gpt-5.2

You can also append reasoning levels: ``:low``, ``:medium``, ``:high``, or ``:xhigh``:

.. code-block:: sh

    gptme "solve this problem" -m openai-subscription/gpt-5.4:high

**Available Models:**

- ``gpt-5.4`` - Latest GPT model with reasoning capabilities (recommended)
- ``gpt-5.3-codex`` - Previous code-optimized variant
- ``gpt-5.3-codex-spark`` - Faster variant of gpt-5.3-codex
- ``gpt-5.2`` - Previous generation GPT model
- ``gpt-5.2-codex`` - Previous code-optimized variant
- ``gpt-5.1-codex-max`` - Maximum capability variant
- ``gpt-5.1-codex`` - Code-optimized
- ``gpt-5.1-codex-mini`` - Smaller code-optimized variant
- ``gpt-5.1`` - Previous generation

.. note::

    This is for **personal development use** with your own ChatGPT Plus/Pro subscription.
    For production or multi-user applications, use the OpenAI Platform API.
    OAuth credentials are stored locally and access tokens are refreshed automatically.

.. rubric:: gptme Managed Service

The ``gptme`` provider connects to the `gptme.ai <https://gptme.ai>`_ managed service, which acts as an OpenAI-compatible LLM proxy/gateway. This gives you access to multiple model providers (Anthropic, OpenAI, etc.) through a single account.

**Setup:**

Authenticate using the Device Flow command:

.. code-block:: sh

    gptme-auth login

This opens your browser to approve access, then stores a token locally at ``~/.config/gptme/auth/gptme-cloud-<hash>.json``. Tokens are refreshed automatically.

**Usage:**

.. code-block:: sh

    gptme "hello" -m gptme/claude-sonnet-4-6
    gptme "hello" -m gptme                    # uses default model

Models are pass-through: ``gptme/<model>`` proxies to the corresponding backend provider.

**Environment variables** (alternative to Device Flow login):

- ``GPTME_CLOUD_API_KEY``: API key for the managed service
- ``GPTME_CLOUD_BASE_URL``: Custom service URL (default: ``https://fleet.gptme.ai/v1``)

**Auth commands:**

.. code-block:: sh

    gptme-auth login               # Login via Device Flow (opens browser)
    gptme-auth login --no-browser  # Print URL instead of opening browser
    gptme-auth status              # Show current login status
    gptme-auth logout              # Remove stored credentials

.. rubric:: Local

You can use local LLM models using any OpenAI API-compatible server.

To achieve that with ``ollama``, install it then run:

.. code-block:: sh

    ollama pull llama3.2:1b
    ollama serve
    OPENAI_BASE_URL="http://127.0.0.1:11434/v1" gptme 'hello' -m local/llama3.2:1b

.. note::

    Small models won't work well with tools, severely limiting the usefulness of gptme. You can find an overview of how different models perform on the :doc:`evals` page.
