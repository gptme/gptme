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

The recommended model for API-key use is **Claude Sonnet 4.6** (``anthropic/claude-sonnet-4-6``, or ``openrouter/anthropic/claude-sonnet-4-6``) for its:

- Strong agentic capabilities
- Strong coder capabilities
- Strong performance across all tool types and formats
- Reasoning capabilities
- Vision & computer use capabilities

If you already pay for a frontier subscription, use it instead of an API key (see :ref:`providers-subscriptions`): **GPT-6 Astra** via ChatGPT Plus/Pro (``openai-subscription/gpt-6-astra``) and **Grok 4.6** via SuperGrok (``grok-subscription/grok-4.6``) are both frontier-class and cost nothing per token.

For high-volume or cost-sensitive work, two open-weight "flash" models hold up well in agentic use for a small fraction of the price:

- **DeepSeek V4.1 Flash** (``openrouter/deepseek/deepseek-v4.1-flash``, or ``deepseek/deepseek-flash`` on the official API; the earlier ``deepseek-v4-flash-0731`` is still hosted by third parties on OpenRouter but no longer by the official endpoint)
- **GLM 5.3 Flash** (``openrouter/z-ai/glm-5.3-flash``)

When a model has multiple OpenRouter hosts, their reliability, speed, and data policies can vary widely; see :ref:`openrouter-hosts` before picking one. V4.1 Flash currently has only the official DeepSeek host.

Decent alternatives include:

- GPT-5.6 Sol / Terra / Luna (``openai/gpt-5.6-sol``, ``openai-subscription/gpt-5.6-sol``)
- Gemini 3.1 Pro (``gemini/gemini-3.1-pro-preview``, ``openrouter/google/gemini-3-pro-preview``)
- Grok 4.6 via the API (``xai/grok-4.6``, ``openrouter/x-ai/grok-4.6``)
- DeepSeek V4 Pro (``openrouter/deepseek/deepseek-v4-pro-0813``, ``deepseek/deepseek-v4-pro``)
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
    gptme "refactor this module for testability" -m anthropic/claude-sonnet-4-6

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

    gptme "hello" -m openrouter/anthropic/claude-sonnet-4-6
    gptme "hello" -m openrouter/deepseek/deepseek-v4-pro
    gptme "hello" -m openrouter/x-ai/grok-4

gptme applies privacy-first defaults for OpenRouter (data collection denied,
provider routing requires full parameter support). See :ref:`openrouter` for
configuration details, quantization controls, and provider pinning.

Set a default model
-------------------

If you mostly use one model, set it once in ``gptme.toml`` (project) or
``~/.config/gptme/config.toml`` (global) instead of passing ``--model`` every time:

.. code-block:: toml

    # gptme.toml or ~/.config/gptme/config.toml
    model = "openrouter/qwen/qwen3-max"

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
    model = "openrouter/qwen/qwen3-max"

.. code-block:: toml

    # coder-agent/gptme.toml — frontier model for complex implementation
    model = "anthropic/claude-sonnet-4-6"

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
