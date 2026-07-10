Local and Alternative Models
============================

This guide covers **Ollama**, **Groq**, and **vLLM** (or any OpenAI-compatible server) —
providers that are not OpenAI, Anthropic, or OpenRouter.

For the full provider list and API keys, see :doc:`providers`. For the ``[[providers]]``
config syntax, see :doc:`custom-providers`.

Ollama (local)
--------------

`Ollama <https://ollama.com/>`_ runs LLMs on your machine. gptme connects through
Ollama's OpenAI-compatible API.

**Quick start:**

.. code-block:: sh

    # Install Ollama (https://ollama.com/download)
    ollama pull llama3.2:3b
    ollama serve

    OPENAI_BASE_URL="http://127.0.0.1:11434/v1" gptme 'hello' -m local/llama3.2:3b

**Persistent config** (``~/.config/gptme/config.toml``):

.. code-block:: toml

    [env]
    OPENAI_BASE_URL = "http://127.0.0.1:11434/v1"
    MODEL = "local/llama3.2:3b"

Or use a named custom provider (see :doc:`custom-providers`):

.. code-block:: toml

    [[providers]]
    name = "ollama"
    base_url = "http://127.0.0.1:11434/v1"
    default_model = "llama3.2:3b"

Then: ``gptme 'hello' -m ollama/llama3.2:3b``

**Model name format:** The name after ``local/`` (or ``ollama/``) must match
``ollama list`` exactly, including the ``:tag`` suffix.

.. code-block:: sh

    ollama list
    gptme 'hi' -m local/llama3.2:3b       # correct
    gptme 'hi' -m local/llama3.2          # wrong if the tag is 3b, not latest

**Common errors:**

+---------------------------+----------------------------------+------------------------------------------+
| Error                     | Cause                            | Fix                                      |
+===========================+==================================+==========================================+
| Connection refused :11434 | Ollama not running               | ``ollama serve``                         |
+---------------------------+----------------------------------+------------------------------------------+
| Unknown model X (warning) | Model not in gptme's known list  | Harmless; the model still works          |
+---------------------------+----------------------------------+------------------------------------------+
| Tool use fails or loops   | Model too small for tool format  | Use 7B+ (e.g. ``llama3.1:8b``, Mistral)  |
+---------------------------+----------------------------------+------------------------------------------+

.. note::

   Models under ~7B parameters rarely follow gptme's tool protocol reliably.
   For agent-style work, prefer at least ``llama3.1:8b`` or ``mistral:7b-instruct``.

Groq
----

`Groq <https://groq.com/>`_ serves open-weight models with a dedicated API (not OpenAI).

**Setup:**

.. code-block:: sh

    export GROQ_API_KEY="gsk_..."
    gptme 'hello' -m groq/llama-3.3-70b-versatile

Interactive setup:

.. code-block:: sh

    gptme '/account setup groq'

Or in ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [env]
    GROQ_API_KEY = "gsk_..."
    MODEL = "groq/llama-3.3-70b-versatile"

**Popular models:**

- ``groq/llama-3.3-70b-versatile`` — fast 70B, good tool use
- ``groq/llama-3.1-8b-instant`` — fastest, lowest cost
- ``groq/deepseek-r1-distill-llama-70b`` — reasoning-oriented

Model list: https://console.groq.com/docs/models

**Common errors:**

+-------------------------------+----------------------------------+--------------------------------------------------+
| Error                         | Cause                            | Fix                                              |
+===============================+==================================+==================================================+
| 401 with ``OPENAI_API_KEY``   | Groq needs its own key           | Set ``GROQ_API_KEY``; use ``groq/<model>``       |
+-------------------------------+----------------------------------+--------------------------------------------------+
| ``OPENAI_API_KEY`` not set    | Default model is OpenAI          | Set ``MODEL`` or ``[models].default`` to Groq    |
+-------------------------------+----------------------------------+--------------------------------------------------+

.. warning::

   Do **not** set ``OPENAI_BASE_URL=https://api.groq.com/openai/v1`` with
   ``OPENAI_API_KEY`` — that returns 401. Use ``GROQ_API_KEY`` and the
   ``groq/<model>`` prefix (see also :doc:`providers`).

vLLM and OpenAI-compatible servers
----------------------------------

Any server exposing ``/v1/chat/completions`` works with the ``local/`` prefix or a
custom ``[[providers]]`` entry.

**Example (vLLM):**

.. code-block:: sh

    python -m vllm.entrypoints.openai.api_server \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --port 8000

    OPENAI_BASE_URL="http://localhost:8000/v1" \
      gptme 'hello' -m local/meta-llama/Llama-3.1-8B-Instruct

**Custom provider:**

.. code-block:: toml

    [[providers]]
    name = "vllm"
    base_url = "http://localhost:8000/v1"
    default_model = "meta-llama/Llama-3.1-8B-Instruct"

.. code-block:: sh

    VLLM_API_KEY="none"   # vLLM often needs no auth
    gptme 'hello' -m vllm/meta-llama/Llama-3.1-8B-Instruct

**Tokenizer in airgapped environments**

gptme may fetch the OpenAI ``cl100k_base`` tokenizer to count tokens. Offline, that
can time out with errors mentioning ``openaipublic.blob.core.windows.net``.

In recent gptme versions, token counting can fall back to a character-based estimate
(~4 characters per token) when the tokenizer download fails.

On older versions, pre-cache tiktoken once while online:

.. code-block:: sh

    pip install tiktoken
    python3 -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

Setting a default model
-----------------------

**Environment variable:**

.. code-block:: sh

    export MODEL="groq/llama-3.3-70b-versatile"
    gptme 'hello'

**Global config** (recommended — see :doc:`config`):

.. code-block:: toml

    [models]
    default = "groq/llama-3.3-70b-versatile"

**Project config** (``gptme.toml`` in the repo root):

.. code-block:: toml

    [env]
    MODEL = "local/llama3.2:3b"

Related discussions
-------------------

Community threads that motivated this page:

- `Ollama setup <https://github.com/gptme/gptme/discussions/177>`_
- `Llama 3.1 70B <https://github.com/gptme/gptme/discussions/178>`_
- `Groq as default <https://github.com/gptme/gptme/discussions/224>`_
- `Groq configuration <https://github.com/gptme/gptme/discussions/230>`_
- `vLLM tokenizer timeouts <https://github.com/gptme/gptme/discussions/559>`_
