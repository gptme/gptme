:audience: user

Models
======

gptme is model-agnostic: it works with any LLM through a single ``--model`` flag, and
you can pick a different model for every task. Use a small, fast model for quick
questions and a powerful reasoning model for complex code — without changing tools,
formats, or workflow.

This page helps you *pick* a model. To set up access to one — API keys,
subscriptions, local servers — see :doc:`providers`.

Recommended models
------------------

For API-key use we recommend **Claude Sonnet 5.5** (``anthropic/claude-sonnet-5-5``, or ``openrouter/anthropic/claude-sonnet-5.5``) as the everyday model, and **Claude Opus 5.5** (``anthropic/claude-opus-5-5``, or ``openrouter/anthropic/claude-opus-5.5``) for the hardest tasks. Both offer:

- Strong agentic capabilities
- Strong coder capabilities
- Strong performance across all tool types and formats
- Reasoning capabilities
- Vision & computer use capabilities

Claude Sonnet 4.6 (``anthropic/claude-sonnet-4-6``), the previous recommendation, was a strong and dependable workhorse and still works well. The 5.5 pair is now the easier recommendation over Sonnet 4.6 and the intermediate releases in between.

If you already pay for a frontier subscription, use it instead of an API key (see :ref:`providers-subscriptions`): **GPT-6 Astra** via ChatGPT Plus/Pro (``openai-subscription/gpt-6-astra``) and **Grok 4.6** via SuperGrok (``grok-subscription/grok-4.6``) are both frontier-class and cost nothing per token.

For high-volume or cost-sensitive work, two open-weight "flash" models hold up well in agentic use for a small fraction of the price:

- **DeepSeek V4.1 Flash** (``openrouter/deepseek/deepseek-v4.1-flash``, or ``deepseek/deepseek-flash`` on DeepSeek's own API). It is the default model on `gptme.ai <https://gptme.ai>`_ and what ``-m openrouter`` resolves to.
- **GLM 5.3 Flash** (``openrouter/z-ai/glm-5.3-flash``)

DeepSeek V4.1 Flash is not limited to DeepSeek's own API: OpenRouter routes it to around 30 third-party hosts as well (Together, Fireworks, DeepInfra, and others). Pin one host, or an ordered list that falls back within itself, by appending ``@`` and OpenRouter provider slugs to the model:

.. code-block:: sh

    gptme -m "openrouter/deepseek/deepseek-v4.1-flash@together"
    gptme -m "openrouter/deepseek/deepseek-v4.1-flash@together,fireworks,deepinfra"

Without a pin, OpenRouter picks among all hosts that pass gptme's privacy defaults (see :ref:`models-data-policy`). Hosts differ in reliability, speed, cache pricing, and data policy; see :ref:`openrouter-hosts` before picking one. The earlier ``deepseek-v4-flash-0731`` is still served by third-party hosts on OpenRouter, but DeepSeek's own API has retired it.

Decent alternatives include:

- GPT-5.6 Sol / Terra / Luna (``openai/gpt-5.6-sol``, ``openai-subscription/gpt-5.6-sol``)
- Gemini 3.1 Pro (``gemini/gemini-3.1-pro-preview``, ``openrouter/google/gemini-3.1-pro-preview``)
- Grok 4.6 via the API (``xai/grok-4.6``, ``openrouter/x-ai/grok-4.6``)
- DeepSeek V4 Pro (``openrouter/deepseek/deepseek-v4-pro-0813``; DeepSeek's own API now serves ``deepseek-v4-pro`` requests with V4.1 Flash)
- Kimi K3 / K2.6 (``moonshot/kimi-k3``, ``openrouter/moonshotai/kimi-k2.6``)
- Qwen3 Max (``openrouter/qwen/qwen3-max``)
- MiniMax M2 (``openrouter/minimax/minimax-m2``)

Some models perform better or worse with different ``--tool-format`` options (``markdown``, ``xml``, or ``tool`` for native tool-calling); see :doc:`tool-formats`.

To see how models actually perform on gptme's eval suites, check the :ref:`model leaderboard <model-leaderboard>`. For an overview of model usage in the wild, see the `OpenRouter app analytics for gptme <https://openrouter.ai/apps?url=https://github.com/gptme/gptme>`_. When you pass only a provider name (``-m anthropic``), gptme uses that provider's :ref:`default model <default-models>`.

Pick a model per session
------------------------

Pass ``--model`` (``-m``) as ``<provider>/<model>`` to choose the model for a single run:

.. code-block:: sh

    # Quick question — small, cheap, fast
    gptme "what does this regex match?" -m openrouter/qwen/qwen3-max

    # Complex coding — powerful reasoning model
    gptme "refactor this module for testability" -m anthropic/claude-sonnet-5-5

    # Use a provider default (no model specified)
    gptme "hello" -m anthropic

List the models gptme knows about at any time:

.. code-block:: sh

    gptme '/models' - '/exit'

The rule of thumb: **match the model to the job.** Triage, summarization, and
quick lookups run fine on small models; multi-step coding and reasoning benefit
from a frontier model. Picking per task keeps cost down without capping capability.

One key, many models: OpenRouter
--------------------------------

`OpenRouter <https://openrouter.ai/>`_ is the easiest way to reach many models
without managing a separate API key for each provider. With one
``OPENROUTER_API_KEY`` you can route to 100+ models from Anthropic, OpenAI, Google,
DeepSeek, xAI, and more:

.. code-block:: sh

    gptme "hello" -m openrouter/anthropic/claude-sonnet-5.5
    gptme "hello" -m openrouter/deepseek/deepseek-v4.1-flash
    gptme "hello" -m openrouter/x-ai/grok-4.6

gptme applies privacy-first defaults for OpenRouter (data collection denied,
provider routing requires full parameter support). See :ref:`openrouter` for
configuration details, quantization controls, and provider pinning.

.. _models-data-policy:

Data policy
~~~~~~~~~~~

**On gptme.ai**, prompts are never routed to a provider that trains on them. The
gptme.ai gateway forces OpenRouter's no-training filter
(``data_collection: "deny"``) on every request, also excludes every provider
OpenRouter lists as training on prompts, including DeepSeek's first-party API,
and serves the default DeepSeek V4.1 Flash only from a vetted allowlist of
no-training, zero-data-retention hosts. A request pinned to a training provider
is rejected rather than silently rerouted.

**When self-hosting**, gptme already sends ``data_collection: "deny"`` to
OpenRouter by default (``OPENROUTER_DATA_COLLECTION``), which skips hosts that
train on prompts, DeepSeek's official endpoint among them. To go further:

- Enforce it account-wide in OpenRouter's `privacy settings
  <https://openrouter.ai/settings/privacy>`_, so it holds for every client using
  your key: turn off providers that may train on inputs, optionally require
  Zero Data Retention endpoints, and add providers to the account-wide ignore list.
- Restrict gptme to hosts you have vetted with an ``@`` pin, or set
  ``OPENROUTER_PROVIDER_ORDER`` to apply the same allowlist to every request
  (see :ref:`openrouter`).
- Keep in mind that a direct provider such as ``deepseek/...`` bypasses
  OpenRouter, so only that provider's own data policy applies; DeepSeek's allows
  training on API inputs.

Set a default model
-------------------

If you mostly use one model, set it once in your global config
(``~/.config/gptme/config.toml``) instead of passing ``--model`` every time:

.. code-block:: toml

    [models]
    default = "openrouter/qwen/qwen3-max"

With a default configured, ``gptme "query"`` uses that model, and ``--model`` still
overrides it per run when you need something stronger or cheaper.

See :doc:`config` for the full config reference.

Per-agent models
----------------

When you run multiple agents — for example a team of agents each handling a
different role — each can have its own model. Set the model in the agent's own
config so a fast routing agent and a reasoning-heavy coding agent can coexist
without per-call flags:

.. code-block:: toml

    # router-agent/gptme.toml — cheap, fast, handles triage and dispatch
    [env]
    MODEL = "openrouter/qwen/qwen3-max"

.. code-block:: toml

    # coder-agent/gptme.toml — frontier model for complex implementation
    [env]
    MODEL = "anthropic/claude-sonnet-5-5"

A model set this way wins over a global ``[models].default``: the project's
``gptme.toml`` is the more specific layer. To override it for a single run, pass
``--model`` in the command that runs the agent, or export ``MODEL`` for that
command. See :ref:`how-model-selection-works` for the full order.

This is how an agent "brain" pins its default model: configure it once in the
agent's config, override per session only when a specific task needs a different
model. No vendor lock-in, no format changes.

See also
--------

- :doc:`providers` — set up access: credentials, subscriptions, default models
- :doc:`providers-supported` — setup details for each built-in provider
- :doc:`providers-custom` — local and OpenAI-compatible servers
- :doc:`tool-formats` — which tool format suits a model
- :doc:`evals` — how models perform on gptme's benchmark suite
