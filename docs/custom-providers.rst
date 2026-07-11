Custom and Local Providers
==========================

This page covers **Ollama**, **vLLM**, and any other
OpenAI-compatible server — plus how to declare a reusable
``[[providers]]`` block in your config so gptme can find them by name.

For the full list of built-in providers and API keys, see :doc:`providers`.

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

Or use a named provider entry (see `Configuration`_ below):

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

vLLM and OpenAI-compatible servers
-----------------------------------

Any server exposing ``/v1/chat/completions`` works with the ``local/`` prefix or a
named ``[[providers]]`` entry.

**Example (vLLM):**

.. code-block:: sh

    python -m vllm.entrypoints.openai.api_server \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --port 8000

    OPENAI_BASE_URL="http://localhost:8000/v1" \
      gptme 'hello' -m local/meta-llama/Llama-3.1-8B-Instruct

**Or as a named provider entry:**

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

Pre-cache tiktoken once while online to avoid this:

.. code-block:: sh

    pip install tiktoken
    python3 -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

.. note::

   Source installs newer than v0.31.0 gracefully fall back to a character-based
   estimate (~4 chars/token) when the download fails. PyPI releases do not yet
   include this fallback, so the pre-cache step is recommended regardless.

Configuration
-------------

Add custom providers to ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [[providers]]
    name = "vllm-local"
    base_url = "http://localhost:8000/v1"
    default_model = "meta-llama/Llama-3.1-8B"

    [[providers]]
    name = "azure-gpt4"
    base_url = "https://my-azure-endpoint.openai.azure.com/openai/deployments"
    api_key_env = "AZURE_API_KEY"
    default_model = "gpt-4"

**Configuration fields:**

================== ======== ====================================================
Field              Required Description
================== ======== ====================================================
``name``            Yes      Provider identifier used in model selection
``base_url``        Yes      Base URL for the OpenAI-compatible API
``api_key``         No       API key directly in config (not recommended)
``api_key_env``     No       Environment variable name containing the API key
``default_model``   No       Default model when only provider name is specified
================== ======== ====================================================

**API key resolution order:**

1. ``api_key = "key-here"`` (not recommended for security)
2. ``api_key_env = "MY_API_KEY"``
3. ``${PROVIDER_NAME}_API_KEY`` (e.g. ``VLLM_API_KEY`` for a provider named ``vllm``)

**Listing configured providers:**

.. code-block:: sh

    gptme-util providers list

Setting a default model
-----------------------

**Environment variable:**

.. code-block:: sh

    export MODEL="local/llama3.2:3b"
    gptme 'hello'

**Global config** (recommended — see :doc:`config`):

.. code-block:: toml

    [models]
    default = "ollama/llama3.2:3b"

**Project config** (``gptme.toml`` in the repo root):

.. code-block:: toml

    [env]
    MODEL = "local/llama3.2:3b"

Backward compatibility
----------------------

The existing ``local`` provider continues to work using the ``OPENAI_BASE_URL``
and ``OPENAI_API_KEY`` environment variables. No changes are required for
existing configurations.

Related discussions
-------------------

Community threads that motivated this page:

- `Ollama setup <https://github.com/gptme/gptme/discussions/177>`_
- `Llama 3.1 70B <https://github.com/gptme/gptme/discussions/178>`_
- `vLLM tokenizer timeouts <https://github.com/gptme/gptme/discussions/559>`_
