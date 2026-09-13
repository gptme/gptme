:audience: user

Providers
=========

We support LLMs from several providers, including OpenAI, Anthropic, OpenRouter, Requesty, Deepseek, Azure, and any OpenAI-compatible server (e.g. ``ollama``, ``llama-cpp-python``).

You can also bring your own subscription instead of an API key: a ChatGPT Plus/Pro plan via :ref:`OpenAI Subscription <openai-subscription>` or a SuperGrok plan via :ref:`Grok Subscription <grok-subscription>`.

.. important::

    A provider or subscription backend is not an agent harness. This page
    configures model access **inside the gptme runtime**. For example,
    ``openai-subscription`` uses ChatGPT subscription access from gptme; it does
    not run the Codex CLI harness. Likewise, ``grok-subscription`` does not run
    the Grok Build harness. See :ref:`agent-runtime-portability` for the
    workspace / harness / model / access distinction.

This page is an overview of how to get model access. To decide *which* model to
use, see :doc:`models`. For details, see:

- :doc:`providers-supported` — setup details for each built-in provider
- :doc:`providers-custom` — Ollama, LM Studio, vLLM, and other OpenAI-compatible servers
- :doc:`providers-integration` — add a new provider as a config entry, plugin package, or core PR
- :doc:`tool-formats` — how tools are presented to the model, and which format to choose

.. toctree::
   :hidden:

   providers-supported
   providers-custom
   providers-integration
   tool-formats

Selecting a provider and model
------------------------------

To select a provider and model, run ``gptme`` with the ``-m``/``--model`` flag set to ``<provider>/<model>``, for example:

.. code-block:: sh

    gptme "hello" -m openai/gpt-5.6-sol
    gptme "hello" -m openai-subscription/gpt-6-astra  # uses your ChatGPT Plus/Pro subscription
    gptme "hello" -m anthropic  # will use provider default
    gptme "hello" -m openrouter/x-ai/grok-4
    gptme "hello" -m openrouter/deepseek/deepseek-v4-flash@together  # pin to Together subprovider
    gptme "hello" -m deepseek/deepseek-v4-flash
    gptme "hello" -m xai/grok-4
    gptme "hello" -m grok-subscription/grok-4.6  # uses your SuperGrok subscription
    gptme "hello" -m gemini/gemini-2.5-flash
    gptme "hello" -m groq/llama-3.3-70b-versatile
    gptme "hello" -m gptme/claude-sonnet-4-6  # use the gptme managed service as router
    gptme "hello" -m local/llama3.2:1b  # uses a local OpenAI-compatible server (e.g. ollama)
    gptme "hello" -m custom/model  # use a custom provider plugin

You can list the models known to gptme using ``gptme '/models' - '/exit'``.

Which tool format a model performs best with also varies by provider and model —
see :doc:`tool-formats` for how to choose one.

Supported providers
-------------------

Built-in providers, the model prefix to use, and how each one authenticates.
Each links to its setup details on :doc:`providers-supported`.

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Provider
     - Model prefix
     - Authentication
   * - :ref:`OpenAI Platform <openai-platform>`
     - ``openai/``
     - ``OPENAI_API_KEY``
   * - :ref:`Anthropic <anthropic>`
     - ``anthropic/``
     - ``ANTHROPIC_API_KEY``
   * - :ref:`OpenRouter <openrouter>`
     - ``openrouter/``
     - ``OPENROUTER_API_KEY``, or browser sign-in with ``/account setup openrouter``
   * - :ref:`Requesty <requesty>`
     - ``requesty/``
     - ``REQUESTY_API_KEY``
   * - :ref:`Groq <groq>`
     - ``groq/``
     - ``GROQ_API_KEY``
   * - :ref:`Google Gemini <gemini>`
     - ``gemini/``
     - ``GEMINI_API_KEY``
   * - :ref:`xAI <xai>`
     - ``xai/``
     - ``XAI_API_KEY``
   * - :ref:`DeepSeek <deepseek>`
     - ``deepseek/``
     - ``DEEPSEEK_API_KEY``
   * - :ref:`Moonshot <moonshot>`
     - ``moonshot/``
     - ``MOONSHOT_API_KEY``
   * - :ref:`NVIDIA <nvidia>`
     - ``nvidia/``
     - ``NVIDIA_API_KEY``
   * - :ref:`Azure OpenAI <azure>`
     - ``azure/``
     - ``AZURE_OPENAI_API_KEY`` and ``AZURE_OPENAI_ENDPOINT``
   * - :ref:`OpenAI Subscription <openai-subscription>`
     - ``openai-subscription/``
     - ChatGPT Plus/Pro sign-in with ``gptme-auth openai-subscription``
   * - :ref:`Grok Subscription <grok-subscription>`
     - ``grok-subscription/``
     - SuperGrok sign-in with ``gptme-auth grok-subscription`` (or an existing ``grok login``)
   * - :ref:`gptme Managed Service <gptme-managed-service>`
     - ``gptme/``
     - ``gptme-auth login``, or ``GPTME_CLOUD_API_KEY``
   * - :ref:`Local server <local-providers>`
     - ``local/``
     - ``OPENAI_BASE_URL`` pointing at an OpenAI-compatible server

.. _default-models:

Default models
--------------

When you pass only a provider name (for example ``gptme -m anthropic``), gptme
uses that provider's default model. This table is generated at docs-build time
from the installed gptme, so it always reflects the current release. The summary
model is the cheaper model used for conversation titles and summaries.

.. command-output:: gptme-util models recommended
   :cwd: ..
   :shell:

.. _providers-subscriptions:

Subscriptions
-------------

Several frontier models are reachable through a consumer subscription instead of a metered API key, which is usually the cheapest way to run gptme on a frontier model:

- **ChatGPT Plus/Pro (Codex)** — ``openai-subscription/gpt-6-astra`` (and the GPT-5.6 family). Authenticate once with ``gptme-auth openai-subscription``.
- **SuperGrok (Grok Build)** — ``grok-subscription/grok-4.6``. Reuses the grok CLI's login, or ``gptme-auth grok-subscription``.
- **Claude Max** — not available: Anthropic does not permit third-party tools on the consumer subscription; use the ``anthropic`` provider with an API key.
- **Cursor** — not supported. Cursor exposes no sanctioned endpoint for using a personal subscription from third-party tools (its Cloud Agents API is separately metered and is not a chat-completions API). Unofficial proxies that reuse the Cursor CLI's login state exist, but gptme does not integrate them.

See :ref:`openai-subscription` and :ref:`grok-subscription` for setup details.

Configuring credentials
-----------------------

To configure provider credentials interactively, run ``/account`` inside gptme:

.. code-block:: text

    /account
    /account setup
    /account setup openrouter

``/account setup openrouter`` starts browser-based OpenRouter sign-in using OAuth / PKCE, stores the resulting key in ``~/.config/gptme/credentials.toml``, and switches the default model to OpenRouter's recommended default.

For providers without OAuth onboarding yet, ``/account setup <provider>`` prompts for the key without putting it in shell history and stores it in ``~/.config/gptme/credentials.toml`` (or ``$XDG_CONFIG_HOME/gptme/credentials.toml`` if set). Supported manual providers currently include ``anthropic``, ``openai``, ``deepseek``, ``gemini``, ``groq``, and ``xai``.

Subscription sign-in (ChatGPT Plus/Pro and SuperGrok) is also offered in the first-run setup when gptme starts without any configured credentials.

You can still use the ``[env]`` section in the :ref:`global-config` file to store API keys using the same format as the environment variables:

- ``OPENAI_API_KEY="your-api-key"``
- ``ANTHROPIC_API_KEY="your-api-key"``
- ``OPENROUTER_API_KEY="your-api-key"``
- ``GEMINI_API_KEY="your-api-key"``
- ``XAI_API_KEY="your-api-key"``
- ``GROQ_API_KEY="your-api-key"``
- ``DEEPSEEK_API_KEY="your-api-key"``

.. _reasoning-effort:

Reasoning effort
----------------

Reasoning models accept a named effort level that trades latency and cost for
more thinking. gptme exposes one knob, ``GPTME_THINKING_EFFORT``, and maps it
to each provider's parameter:

.. list-table::
   :header-rows: 1

   * - Provider
     - Request parameter
     - Accepted levels
   * - Anthropic
     - ``thinking.budget_tokens`` (and ``output_config.effort`` on SDK >= 0.77)
     - ``low``, ``medium``, ``high``, ``xhigh``, ``max``
   * - OpenAI (Chat Completions)
     - ``reasoning_effort``
     - ``none``, ``minimal``, ``low``, ``medium``, ``high``, ``xhigh``, ``max``
   * - OpenAI (Responses API)
     - ``reasoning.effort``
     - same as above
   * - OpenRouter
     - ``reasoning.effort`` (replaces the default ``reasoning.max_tokens`` budget)
     - ``none``, ``minimal``, ``low``, ``medium``, ``high``, ``xhigh``
   * - Moonshot Kimi K3
     - ``reasoning_effort``
     - ``low``, ``high``, ``max``
   * - OpenAI Subscription (Codex)
     - ``reasoning.effort`` from the model ``:level`` suffix (default ``medium``)
     - ``low``, ``medium``, ``high``, ``xhigh``

Which subset a specific model accepts (for example ``none`` on gpt-5.1+,
``xhigh`` on gpt-5.2+) is enforced by the provider; gptme only rejects levels
the provider never accepts. Models without reasoning support ignore the
variable, so it is safe to leave set across model switches.

.. code-block:: sh

    GPTME_THINKING_EFFORT=high gptme "prove this" -m openai/gpt-5.5
    GPTME_THINKING_EFFORT=low gptme "rename the variable" -m openrouter/deepseek/deepseek-r1

Every assistant message records what happened, so session logs are not blind
to effort:

- ``metadata.reasoning_effort`` - the level that shaped the request (only set
  when one applied; absent means the provider default).

- ``metadata.usage.reasoning_tokens`` - reasoning tokens reported by the
  provider (OpenAI, OpenRouter, Codex). Anthropic bills thinking inside
  ``output_tokens`` and does not report it separately.

Custom, local, and plugin providers
-----------------------------------

Any OpenAI-compatible server (Ollama, LM Studio, vLLM, a private proxy) can be
used with the ``local/`` prefix or declared as a named ``[[providers]]`` entry
in your config — see :doc:`providers-custom`.

Third-party packages can also register providers through the ``gptme.providers``
entry point, making them available right after installation:

.. code-block:: sh

    pip install gptme-provider-minimax
    gptme "hello" -m minimax/MiniMax-M3

See :doc:`providers-integration` to write one.
