Provider Integration Guide
==========================

This guide explains how to connect a new LLM provider to gptme — whether as a
standalone pip-installable plugin, a user-level custom provider config, or a
built-in provider PR.

.. contents:: Table of Contents
   :local:
   :depth: 2


Choosing the Right Integration Path
------------------------------------

+-------------------------------+--------------------------------------------------------+--------------------+
| Scenario                      | Recommended path                                       | Review required?   |
+===============================+========================================================+====================+
| Personal/team API endpoint    | :ref:`custom-provider-config`                          | No                 |
+-------------------------------+--------------------------------------------------------+--------------------+
| Shareable OpenAI-compatible   | :ref:`provider-plugin-package`                         | Only your package  |
| provider (e.g. MiniMax, Chutes)|                                                       |                    |
+-------------------------------+--------------------------------------------------------+--------------------+
| Widely-used provider that     | :ref:`builtin-provider-pr`                             | gptme core PR      |
| many gptme users will want    |                                                        |                    |
+-------------------------------+--------------------------------------------------------+--------------------+

Start with a plugin package unless the provider has broad community demand —
it lets you iterate quickly without waiting for a core PR review.


.. _custom-provider-config:

Option 1: Custom Provider Config
---------------------------------

For a personal or team endpoint (Ollama, vLLM, a private proxy), add a
``[[providers]]`` block to ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [[providers]]
    name = "mycompany"
    base_url = "https://llm.mycompany.internal/v1"
    api_key_env = "MYCOMPANY_API_KEY"        # env var holding the key
    default_model = "llama3.1-8b"

Then use it with ``gptme 'hello' -m mycompany/llama3.1-8b``.

See :doc:`providers-custom` for the full config reference.


.. _provider-plugin-package:

Option 2: Provider Plugin Package
-----------------------------------

A provider plugin is a Python package you publish to PyPI.  Once installed,
users can use your provider without any config file changes.

Minimum working example
~~~~~~~~~~~~~~~~~~~~~~~~

Create a package (e.g. ``gptme-provider-acme``) with a single module:

.. code-block:: python

    # src/gptme_provider_acme/__init__.py
    from gptme.llm.models import ModelMeta, ProviderPlugin

    provider = ProviderPlugin(
        name="acme",
        api_key_env="ACME_API_KEY",
        base_url="https://api.acme.ai/v1",
        models=[
            ModelMeta(
                provider="unknown",
                model="acme/turbo-v1",
                context=128_000,
                max_output=4_096,
                price_input=0.10,   # USD per 1M input tokens
                price_output=0.40,  # USD per 1M output tokens
                supports_vision=True,
            ),
            ModelMeta(
                provider="unknown",
                model="acme/mini-v1",
                context=32_000,
                max_output=2_048,
                price_input=0.02,
                price_output=0.08,
            ),
        ],
    )

Declare the entry point in ``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."gptme.providers"]
    acme = "gptme_provider_acme:provider"

After ``pip install gptme-provider-acme``:

.. code-block:: bash

    ACME_API_KEY="sk-..." gptme 'hello' -m acme/turbo-v1


Required ProviderPlugin fields
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

+------------------+----------+-------------------------------------------------------+
| Field            | Required | Description                                           |
+==================+==========+=======================================================+
| ``name``         | Yes      | Unique provider name used as model prefix             |
+------------------+----------+-------------------------------------------------------+
| ``api_key_env``  | Yes      | Environment variable that holds the API key           |
+------------------+----------+-------------------------------------------------------+
| ``base_url``     | Yes      | Base URL for the OpenAI-compatible endpoint           |
+------------------+----------+-------------------------------------------------------+
| ``models``       | No       | List of ``ModelMeta`` objects (empty = 128k fallback) |
+------------------+----------+-------------------------------------------------------+
| ``init``         | No       | ``(Config) -> None`` - custom auth, must register     |
|                  |          | an OpenAI client before returning                     |
+------------------+----------+-------------------------------------------------------+

If ``init`` is omitted, gptme auto-initialises an OpenAI client using
``base_url`` and the key from ``api_key_env``.


ModelMeta capability fields
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When declaring models, set these flags to match your provider's capabilities.
gptme uses them to route requests correctly and render UI hints.

+---------------------------------------+----------+---------------------------------------------+
| Field                                 | Default  | When to set ``True``                        |
+=======================================+==========+=============================================+
| ``context``                           | -        | **Required.** Max context window (tokens)   |
+---------------------------------------+----------+---------------------------------------------+
| ``max_output``                        | ``None`` | Max tokens the model returns (if capped)    |
+---------------------------------------+----------+---------------------------------------------+
| ``supports_vision``                   | False    | Model accepts image inputs                  |
+---------------------------------------+----------+---------------------------------------------+
| ``supports_reasoning``                | False    | Model has native chain-of-thought reasoning |
+---------------------------------------+----------+---------------------------------------------+
| ``supports_streaming``                | True     | Model streams tokens (nearly universal)     |
+---------------------------------------+----------+---------------------------------------------+
| ``supports_parallel_tool_calls``      | False    | Model can emit multiple tool calls at once  |
+---------------------------------------+----------+---------------------------------------------+
| ``supports_strict_tools``             | False    | Supports OpenAI ``strict=True`` in schemas  |
+---------------------------------------+----------+---------------------------------------------+
| ``supports_responses_api``            | False    | Compatible with the OpenAI Responses API    |
+---------------------------------------+----------+---------------------------------------------+
| ``pricing_type``                      | per_token| ``"subscription"`` for flat-rate plans      |
+---------------------------------------+----------+---------------------------------------------+
| ``price_input`` / ``price_output``    | 0        | USD per 1M tokens (0 = free/subscription)   |
+---------------------------------------+----------+---------------------------------------------+


Custom auth (subscription-backed providers)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If your provider uses OAuth or a non-API-key auth flow, supply a custom
``init`` function.  It **must** register an OpenAI client before returning:

.. code-block:: python

    def _init(config):
        token = load_my_token()   # read from ~/.myapp/auth.json, etc.
        from gptme.llm.llm_openai import _init_openai_client
        _init_openai_client(
            "myprovider",
            api_key=token,
            base_url="https://api.myprovider.ai/v1",
        )

    provider = ProviderPlugin(
        name="myprovider",
        api_key_env="MYPROVIDER_API_KEY",   # keep as fallback even with custom init
        base_url="https://api.myprovider.ai/v1",
        models=[...],
        init=_init,
    )

If ``init`` runs but does not register a client, gptme raises ``RuntimeError``.
See ``gptme/llm/llm_grok_subscription.py`` for a full OAuth PKCE example.


.. _testing-your-plugin:

Testing Your Plugin
~~~~~~~~~~~~~~~~~~~~

Copy this test scaffold into your package's ``tests/`` directory and adapt it:

.. literalinclude:: ../tests/fixtures/provider_test_scaffold.py
   :language: python

Run with:

.. code-block:: bash

    pytest tests/test_my_provider.py -v

All tests in the scaffold run **without** hitting the real API — they mock the
OpenAI client layer.  Before publishing, also run a live smoke test.  Two env
vars are required to prevent accidental API charges:

.. code-block:: bash

    ACME_API_KEY="sk-..." GPTME_RUN_LIVE_TESTS=1 pytest tests/ -m live -v


Unified Plugin System
~~~~~~~~~~~~~~~~~~~~~~

If your package also provides gptme :doc:`tools <tools>`, :doc:`hooks <hooks>`,
or :doc:`commands <commands>`, register via the unified ``gptme.plugins``
entry-point group instead, which lets a single package provide everything:

.. code-block:: python

    # src/gptme_plugin_acme/plugin.py
    from gptme.plugins.plugin import GptmePlugin
    from .provider import provider

    plugin = GptmePlugin(
        name="acme",
        provider=provider,      # the ProviderPlugin from above
        tools=[...],            # optional
    )

.. code-block:: toml

    [project.entry-points."gptme.plugins"]
    acme = "gptme_plugin_acme.plugin:plugin"

See :doc:`plugins` for the full unified plugin reference.


Package naming
~~~~~~~~~~~~~~

We recommend the convention:

- **Provider only**: ``gptme-provider-<name>`` (e.g. ``gptme-provider-minimax``)
- **Full plugin**: ``gptme-plugin-<name>``


.. _builtin-provider-pr:

Option 3: Built-in Provider PR
--------------------------------

Add the provider to gptme core when:

* It is widely requested by the community (see open issues/discussions)

* It requires special auth that benefits from gptme's built-in ``gptme auth``
  flow (e.g. OAuth PKCE, subscription tokens)

* Its model metadata (capabilities, pricing) is stable and publicly documented

Steps
~~~~~~

1. **Create** ``gptme/llm/llm_<name>.py`` following the pattern in
   ``llm_openai.py`` (for standard API keys) or ``llm_grok_subscription.py``
   (for OAuth/subscription flows).

2. **Register** the provider name in ``gptme/llm/models/types.py``:

   .. code-block:: python

       BuiltinProvider = Literal[
           ...,
           "myprovider",   # ← add here
       ]

   If it uses the OpenAI client path, also add to ``PROVIDERS_OPENAI``.

3. **Add models** to ``gptme/llm/models/openai_models.py`` (for OpenAI-compat
   providers) or create a dedicated ``llm_<name>_models.py``.

4. **Wire init** — in ``gptme/llm/__init__.py``, add a branch in ``init_llm()``
   to call ``llm_<name>.init(config)``.

5. **Add auth support** — if the provider needs ``gptme auth <name>``, add a
   branch in ``gptme/cli/auth.py``.

6. **Write tests** — add ``tests/test_llm_<name>.py`` covering at minimum:

   - Auth/init with a mocked HTTP response
   - Model listing resolves correctly
   - A streaming response produces expected message chunks

7. **Update docs** — add the provider to ``docs/providers.rst``.

8. **Open a PR** with the title ``feat(providers): add <name> provider``.

Before opening the PR, run:

.. code-block:: bash

    make typecheck
    make test
    make lint


Common Pitfalls
----------------

Model IDs
~~~~~~~~~~

Use the provider-prefixed form in ``ModelMeta.model`` — not bare model names:

.. code-block:: python

    # ✅ Correct
    ModelMeta(provider="unknown", model="acme/turbo-v1", context=128_000)

    # ❌ Wrong — provider prefix missing
    ModelMeta(provider="unknown", model="turbo-v1", context=128_000)

The prefix must match your ``ProviderPlugin.name`` so that
``get_model("acme/turbo-v1")`` can look up the right plugin.

Custom ``init`` must register a client
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you provide ``init``, it must call ``_init_openai_client()`` or an
equivalent before returning.  Failing to do so raises ``RuntimeError`` at
startup and blocks all requests to your provider.

Streaming
~~~~~~~~~~

All built-in gptme providers stream by default.  The ``supports_streaming``
field in ``ModelMeta`` is a **metadata-only** flag — it affects UI hints and
capability-check display, but does **not** change how the underlying OpenAI
client path issues requests.  The transport currently sends ``stream=True``
unconditionally.

If your endpoint does not support streaming and rejects ``stream=True``:

- Configure a proxy layer (e.g. Nginx, Caddy, or LiteLLM) that converts
  streaming calls to blocking responses; or

- `Open an issue <https://github.com/gptme/gptme/issues>`_ to request native
  non-streaming support.

Setting ``supports_streaming=False`` in ``ModelMeta`` documents the limitation
and suppresses misleading UI streaming indicators, but will not prevent the
``stream=True`` parameter from being sent to your endpoint.

Vision inputs
~~~~~~~~~~~~~~

If your provider supports image inputs (``supports_vision=True``), it must
accept the standard OpenAI ``image_url`` content-part format — gptme passes
images through without transcoding.


Related Documentation
-----------------------

- :doc:`providers` — full list of built-in providers and API keys
- :doc:`providers-custom` — custom Ollama/vLLM config reference
- :doc:`plugins` — unified plugin system (tools + hooks + provider in one package)
- :doc:`contributing` — general contribution guide
- `Provider Plugin tests <https://github.com/gptme/gptme/blob/master/tests/test_provider_plugins.py>`_
  — reference test suite for the plugin system
