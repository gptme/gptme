:audience: power-user

Supported Providers
===================

Setup details for each built-in provider. For how to select a model and store
credentials, start with the :doc:`providers` overview. For Ollama, LM Studio,
vLLM, and other OpenAI-compatible servers, see :doc:`providers-custom`.

.. _api-providers:

API providers
-------------

These providers authenticate with an API key. Providers without a section
below (Anthropic, Gemini, xAI, DeepSeek, Moonshot, NVIDIA, Azure) only need
their key — see the table in :ref:`providers:Supported providers`.

.. _openai-platform:

OpenAI Platform
~~~~~~~~~~~~~~~

Use the direct OpenAI Platform provider with ``openai/<model>``:

.. code-block:: sh

    gptme "hello" -m openai/gpt-4o
    gptme "hello" -m openai/gpt-5
    gptme "fix this bug" -m openai/gpt-5.5

GPT-5-class ``openai/*`` models (``gpt-5``, ``gpt-5.5``, ``gpt-5-mini``, and
``gpt-5-nano``) and o-series models automatically use the OpenAI Responses API.
gptme routes these models through ``/v1/responses`` by default:

.. code-block:: sh

    export OPENAI_API_KEY="your-api-key"
    gptme "solve this problem" -m openai/gpt-5

Non-GPT-5 models, proxy providers such as OpenRouter, and other
OpenAI-compatible backends continue using the chat-completions path.

To force the legacy chat-completions path for debugging or comparison, set
``GPTME_OPENAI_RESPONSES_API=0``:

.. code-block:: sh

    export GPTME_OPENAI_RESPONSES_API=0
    gptme "solve this problem" -m openai/gpt-5

In addition to ``0``, the flag also accepts ``false``, ``no``, and ``off`` as
falsy values.

.. note::

    This flag only affects the direct ``openai`` provider. The
    ``openai-subscription`` provider uses its own Responses API path by
    default.

.. _anthropic:

Anthropic
~~~~~~~~~

Use Claude models through Anthropic's native API with ``anthropic/<model>``. gptme
applies prompt caching automatically, which substantially reduces costs in long
conversations.

.. code-block:: sh

    export ANTHROPIC_API_KEY="your-api-key"   # or: gptme '/account setup anthropic'
    gptme "hello" -m anthropic/claude-sonnet-4-6

Set ``GPTME_ANTHROPIC_WEB_SEARCH=true`` to let Claude use Anthropic's native web
search. A Claude Max subscription can't be used from gptme; see
:ref:`providers-subscriptions`.

.. _gemini:

Google Gemini
~~~~~~~~~~~~~

Use Gemini models with ``gemini/<model>``, through Google's OpenAI-compatible
endpoint. Get an API key at `Google AI Studio <https://aistudio.google.com/apikey>`__;
a free tier is available.

.. code-block:: sh

    export GEMINI_API_KEY="your-api-key"   # or: gptme '/account setup gemini'
    gptme "hello" -m gemini/gemini-3.1-pro-preview

.. _xai:

xAI
~~~

Use Grok models with an xAI API key via ``xai/<model>``. Get a key at
`console.x.ai <https://console.x.ai>`__, or use a SuperGrok plan instead with
:ref:`grok-subscription`.

.. code-block:: sh

    export XAI_API_KEY="your-api-key"   # or: gptme '/account setup xai'
    gptme "hello" -m xai/grok-4.6

.. _deepseek:

DeepSeek
~~~~~~~~

Use DeepSeek's official API with ``deepseek/<model>``:

.. code-block:: sh

    export DEEPSEEK_API_KEY="your-api-key"   # or: gptme '/account setup deepseek'
    gptme "hello" -m deepseek/deepseek-v4-flash

DeepSeek models are also available through :ref:`openrouter`, where hosts differ
in reliability and data policy; see :ref:`openrouter-hosts`.

.. _moonshot:

Moonshot
~~~~~~~~

Use Moonshot's Kimi models with ``moonshot/<model>``, such as ``kimi-k3``,
``kimi-k2.6``, or ``kimi-k2``:

.. code-block:: sh

    export MOONSHOT_API_KEY="your-api-key"
    gptme "hello" -m moonshot/kimi-k3

Kimi K3 accepts the ``low``, ``high``, and ``max`` levels of
``GPTME_THINKING_EFFORT``; see :ref:`reasoning-effort`.

.. _nvidia:

NVIDIA
~~~~~~

Use models from the `NVIDIA API catalog <https://build.nvidia.com/>`__ with
``nvidia/<model-id>``, where ``<model-id>`` is the ID shown in the catalog:

.. code-block:: sh

    export NVIDIA_API_KEY="your-api-key"
    gptme "hello" -m nvidia/<model-id>

gptme has no built-in model metadata for NVIDIA, so it uses generic defaults for
context size and pricing.

.. _azure:

Azure OpenAI
~~~~~~~~~~~~

Use OpenAI models deployed on Azure with ``azure/<deployment-name>``. Both an API
key and your resource endpoint are required:

.. code-block:: sh

    export AZURE_OPENAI_API_KEY="your-api-key"
    export AZURE_OPENAI_ENDPOINT="https://<resource>.openai.azure.com"
    gptme "hello" -m azure/<deployment-name>

As with NVIDIA, gptme has no built-in model metadata for Azure deployments.

.. _openrouter:

OpenRouter
~~~~~~~~~~

`OpenRouter <https://openrouter.ai/>`__ provides access to 100+ models through a single API key. gptme applies sensible defaults for OpenRouter requests:

- **Provider routing**: ``require_parameters`` is enabled, ensuring the routed provider supports all request parameters (tools, response format, etc.). This prevents silent failures when OpenRouter falls back to a provider that doesn't support function calling.
- **Privacy**: ``data_collection`` defaults to ``"deny"``, preventing providers from training on your data. This aligns with gptme's privacy-first philosophy. The flag is only sent when extended reasoning is off; for reasoning-capable models (most current open-weight models) pin providers explicitly with the options below, since OpenRouter's default routing may otherwise pick a provider that trains on prompts.
- **Provider override**: Use ``model@provider`` syntax to pin a specific backend (e.g. ``anthropic/claude-sonnet-4-6@anthropic``). A comma-separated list (``model@together,fireworks``) is an ordered allowlist: OpenRouter tries the first, falls back to the next on rate limits or outages, and never routes outside the list. Each model page on OpenRouter has a *Providers* tab listing per-provider pricing, uptime, and data policy (training / prompt retention).
- **Default provider allowlist**: ``OPENROUTER_PROVIDER_ORDER`` applies the same ordered allowlist to every request that has no ``@`` pin, so a vetted set of subproviders can be the default for all models.
- **Quantization**: Optionally restrict to specific precision levels (e.g. ``fp16`` for quality, ``int4`` for cost savings). Set ``OPENROUTER_QUANTIZATION`` to a comma-separated list of accepted levels.

**Configuration:**

.. code-block:: toml

    # In gptme.toml or ~/.config/gptme/config.toml
    [env]
    OPENROUTER_API_KEY = "your-api-key"

    # Override data collection preference (default: "deny")
    # Set to "allow" if you need providers that require data collection consent
    OPENROUTER_DATA_COLLECTION = "allow"

    # Default ordered provider allowlist for requests without a model@provider pin.
    # Slugs are the OpenRouter provider ids shown on each model's Providers tab.
    OPENROUTER_PROVIDER_ORDER = "together,fireworks"

    # Restrict to specific quantization levels (optional)
    # Common values: fp16, bf16, fp8, int8, int4, unknown
    OPENROUTER_QUANTIZATION = "fp16,bf16"

.. _openrouter-hosts:

Choosing a subprovider
^^^^^^^^^^^^^^^^^^^^^^

Open-weight models are served by many providers on OpenRouter, and the provider matters as much as the model: quality (quantization), latency, uptime, cache pricing, and data policy all differ. Pin a provider with ``model@provider``, or an ordered allowlist with ``model@a,b`` (falls back within the list on rate limits), and set ``OPENROUTER_PROVIDER_ORDER`` to apply a default allowlist to every request. See the configuration options above.

What we have found (verified 2026-09-09; re-check the model's *Providers* tab on OpenRouter, this changes often):

- **The official model-developer endpoints are the most reliable path** (``@deepseek`` for DeepSeek, ``@z-ai`` for GLM). They have the best uptime, are among the fastest, and have the best cache-read pricing (DeepSeek official charges ~3% of the input price for cached prefixes, which dominates cost in long agent sessions). Both are what gptme's own automation runs on.
- **Data policy differs.** DeepSeek's official endpoint retains prompts and trains on them, per its policy. Z.AI's official endpoint is listed as *no training, no prompt retention*. Third-party hosts are almost all listed as *no training*, but a handful retain prompts (Alibaba, Baidu, Cloudflare, GMICloud, StreamLake at the time of writing).
- **Third-party hosts are not battle-tested and rate-limit under load.** In a same-day probe of ten DeepSeek V4 Flash hosts and ten GLM 5.3 Flash hosts, a third of them answered ``429 rate-limited upstream`` on a two-request burst, and OpenRouter's default price-first routing lands on the slowest host. Of the no-training hosts, ``together``, ``fireworks``, and ``inceptron`` served DeepSeek V4 Flash correctly with working prompt caching in both probes; ``fireworks``, ``deepinfra``, ``siliconflow``, and ``sail-research`` did the same for GLM 5.3 Flash. Treat these as a starting allowlist, not a recommendation: gptme's own sessions have only exercised the official endpoints (and OpenInference and Baidu for DeepSeek) at volume.
- **Prompt caching is what makes these models cheap.** All of the hosts above billed cache hits at the discounted rate on the second request, regardless of what OpenRouter's ``supports_implicit_caching`` flag said. Compare *cache-read* prices, not just input prices.

A privacy-preserving DeepSeek setup therefore looks like::

    gptme -m "openrouter/deepseek/deepseek-v4-flash-0731@together,fireworks,inceptron"

while GLM 5.3 Flash can simply use its official endpoint::

    gptme -m openrouter/z-ai/glm-5.3-flash@z-ai

Note that pricing for models varies widely when accounting for caching, making some providers much cheaper than others. Anthropic is known and tested to cache well, significantly reducing costs for conversations with many turns.

.. _requesty:

Requesty
~~~~~~~~

`Requesty <https://requesty.ai/>`__ is an OpenAI-compatible LLM gateway that routes to many models through a single API key, using the same ``provider/model`` naming as OpenRouter (e.g. ``requesty/openai/gpt-4o-mini``, ``requesty/anthropic/claude-sonnet-4-5``). It is reached through the standard OpenAI-compatible client path.

**Configuration:**

.. code-block:: toml

    # In gptme.toml or ~/.config/gptme/config.toml
    [env]
    REQUESTY_API_KEY = "your-api-key"

Get an API key at https://app.requesty.ai/api-keys. See https://docs.requesty.ai for details.

.. _groq:

Groq
~~~~

`Groq <https://groq.com/>`__ provides fast inference for open-source models via its own API key — **not** through the ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` pattern.

**Configuration:**

.. code-block:: sh

    export GROQ_API_KEY="gsk_..."
    gptme "hello" -m groq/llama-3.3-70b-versatile

Or store the key via the interactive setup:

.. code-block:: sh

    gptme '/account setup groq'

Or in ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [env]
    GROQ_API_KEY = "gsk_..."

.. note::

    Using ``OPENAI_BASE_URL=https://api.groq.com/openai/v1`` with ``OPENAI_API_KEY``
    will return a 401 — Groq requires its own ``GROQ_API_KEY``.
    The ``groq/<model>`` provider prefix handles this automatically.

Popular Groq models:

- ``groq/llama-3.3-70b-versatile`` — fast 70B Llama 3.3
- ``groq/llama-3.1-8b-instant`` — fastest, smallest

Subscriptions
-------------

Use a subscription you already pay for instead of an API key.

.. _openai-subscription:

OpenAI Subscription
~~~~~~~~~~~~~~~~~~~

You can use your existing ChatGPT Plus/Pro subscription with gptme. This uses the ChatGPT backend API (Codex endpoint) instead of the OpenAI Platform API, allowing you to leverage your subscription for development.

**Setup:**

Authenticate using the OAuth command (opens browser for login):

.. code-block:: sh

    gptme-auth openai-subscription

This stores credentials locally at ``~/.config/gptme/oauth/openai_subscription.json``.
Access tokens are automatically refreshed before expiry, so you only need to authenticate once.

**Usage:**

.. code-block:: sh

    gptme "hello" -m openai-subscription/gpt-6-astra
    gptme "hello" -m openai-subscription/gpt-5.6-sol

You can also append reasoning levels: ``:low``, ``:medium``, ``:high``, or ``:xhigh``:

.. code-block:: sh

    gptme "solve this problem" -m openai-subscription/gpt-6-astra:high

**Available Models:**

- ``gpt-6-astra`` - Current flagship (released 2026-09-03). Rolling out to Codex on Plus/Pro; if the endpoint reports that the model needs a newer client, re-authenticate with ``gptme-auth openai-subscription`` to refresh the token
- ``gpt-5.6-sol`` / ``gpt-5.6-terra`` / ``gpt-5.6-luna`` - GPT-5.6 family (flagship / balanced / fast)
- ``gpt-5.5-pro`` - Previous flagship with maximum reasoning compute (Responses API only)
- ``gpt-5.4`` - Previous flagship with reasoning capabilities
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

.. _grok-subscription:

Grok Subscription
~~~~~~~~~~~~~~~~~

You can use your existing SuperGrok subscription (`grok.com <https://grok.com>`_) with gptme, instead of an xAI API key. This uses the same subscription endpoint as the grok CLI.

**Setup:**

If you have the grok CLI installed and have run ``grok login``, gptme automatically reuses those tokens (from ``~/.grok/auth.json``) with no extra steps.

Otherwise, authenticate directly using the OAuth command (opens browser for login):

.. code-block:: sh

    gptme-auth grok-subscription

This stores credentials locally at ``~/.config/gptme/oauth/grok_subscription.json``.
Access tokens are automatically refreshed before expiry, and refreshed tokens are synced back to the grok CLI's auth file when present.

**Usage:**

.. code-block:: sh

    gptme "hello" -m grok-subscription/grok-4.6
    gptme "hello" -m grok-subscription/grok-4.5

**Available Models:**

- ``grok-4.6`` - Current frontier model (500K context, vision, reasoning)
- ``grok-4.5`` - Previous frontier model (500K context, vision, reasoning)

The subscription endpoint is OpenAI-compatible and supports native function calling (``--tool-format tool``).

.. note::

    This is for **personal development use** with your own SuperGrok subscription.
    For production or multi-user applications, use the xAI Platform API (``xai`` provider) with an API key from `console.x.ai <https://console.x.ai>`_.

.. _gptme-managed-service:

gptme Managed Service
---------------------

The ``gptme`` provider connects to the `gptme.ai <https://gptme.ai>`_ managed service, which acts as an OpenAI-compatible LLM proxy/gateway/router. This gives you access to multiple model providers (Anthropic, OpenAI, etc.) through a single account.

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

.. _local-providers:

Local and OpenAI-compatible servers
-----------------------------------

You can use local LLM models using any OpenAI API-compatible server.

To achieve that with ``ollama``, install it then run:

.. code-block:: sh

    ollama pull llama3.2:1b
    ollama serve
    OPENAI_BASE_URL="http://127.0.0.1:11434/v1" gptme 'hello' -m local/llama3.2:1b

.. note::

    Small models won't work well with tools, severely limiting the usefulness of gptme. You can find an overview of how different models perform on the :doc:`evals` page.

For a guided walkthrough, see :ref:`local-models` in Getting Started. For
LM Studio, vLLM, named ``[[providers]]`` entries, and troubleshooting, see
:doc:`providers-custom`.
