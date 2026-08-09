Custom and Local Providers
==========================

This page covers **Ollama**, **LM Studio**, **vLLM**, and any other
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

**Verify tool use is working:**

After setup, run a real tool call to confirm the full stack works (not just text):

.. code-block:: sh

    OPENAI_BASE_URL="http://127.0.0.1:11434/v1" \
      gptme 'list the files in the current directory' -m local/llama3.1:8b

If gptme runs a shell command and shows real filenames, tool calling works. If the
model just *describes* what it would do without executing anything, the model is
too small or not following the tool protocol — try a larger or more capable model.

LM Studio (local)
-----------------

`LM Studio <https://lmstudio.ai/>`_ is a desktop app for running models locally.
It exposes an OpenAI-compatible server that gptme connects to directly.

**Quick start:**

1. Download `LM Studio <https://lmstudio.ai/>`_ and open it

2. In the **Models** tab, download a 7B+ instruction model
   (e.g. *Llama 3.1 8B Instruct Q4_K_M* — a good balance of speed and tool use)

3. Open the **Local Server** tab (``↔`` icon), load the model, and click **Start Server**

.. code-block:: sh

    # Default server address — no API key required
    OPENAI_BASE_URL="http://localhost:1234/v1" gptme 'hello' -m local/lmstudio

**Named provider config** (``~/.config/gptme/config.toml``):

.. code-block:: toml

    [[providers]]
    name = "lmstudio"
    base_url = "http://localhost:1234/v1"
    default_model = "local-model"

Then: ``gptme 'hello' -m lmstudio``

.. note::

   LM Studio's server serves whichever model is currently loaded, regardless of
   the model name sent in requests. The ``default_model`` value is required by
   gptme's provider resolution — any string works as a placeholder since LM
   Studio ignores the model name in requests (and echoes it back in responses).

**Common errors:**

Connection refused :1234
  **Cause:** Server not started
  **Fix:** Open LM Studio → Local Server → Start

Empty or broken responses
  **Cause:** No model loaded in server tab
  **Fix:** Load a model before starting the server

Tool use fails or loops
  **Cause:** Model too small for tool format
  **Fix:** Use a 7B+ instruction-tuned model; or try a different tool format (``gptme --tool-format xml``)

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

.. _paritok-compression:

Paritok — context compression
-----------------------------

`Paritok <https://github.com/Paritok-official/paritok-4b-v1>`_ is a drop-in
compression proxy. It sits between gptme and your real provider and, on every
request, compresses file reads and tool outputs and summarizes stale history —
then forwards upstream, billed on the *compressed* tokens. Nothing is
permanently discarded: the model can pull back any exact original on demand, so
compression is lossless from the agent's point of view.

Paritok speaks both the OpenAI Chat Completions API and the Anthropic Messages
API, so you wire it into gptme with **no code changes**.

.. important::

   Paritok compresses reads that arrive as **native tool calls**. Run gptme with
   ``--tool-format tool`` (native function-calling) so the ``read`` tool's output
   is a real tool message. With the default ``markdown`` tool-format the file
   content is plain message text and Paritok passes it through unchanged — you
   get the proxy but no savings.

Start the proxy
~~~~~~~~~~~~~~~

Install with the ``proxy`` extra (pulls in the gateway server + the embedding
tool-selector) and start it. First run pulls the ~2.5 GB Paritok-4B model via
Ollama and serves on port ``8080``:

.. code-block:: bash

    pip install "paritok[proxy]"
    paritok up

**No local GPU / no Ollama?** Use the hosted Paritok GPU server instead — create a
starter config, set ``use_gpu_server: true`` and your API key, then start as usual:

.. code-block:: bash

    paritok init        # writes paritok.yaml
    # in paritok.yaml:  use_gpu_server: true  +  gpu_server.api_key: "<your key>"
    paritok up          # now compresses via the hosted GPU, no model download

An API key is created at https://paritok.com (dashboard → API keys).

OpenAI-compatible models
~~~~~~~~~~~~~~~~~~~~~~~~

Add a custom provider pointing at the proxy, in ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [[providers]]
    name = "paritok"
    base_url = "http://127.0.0.1:8080/v1"
    api_key_env = "OPENAI_API_KEY"   # your real upstream key; Paritok forwards it
    default_model = "gpt-4o"          # any model your upstream serves

.. code-block:: bash

    gptme --tool-format tool -m paritok/gpt-4o 'summarize the largest file in this repo'

Claude models
~~~~~~~~~~~~~

Custom providers are OpenAI-shaped, so Claude routes through the built-in
``anthropic`` provider plus gptme's global proxy override ``LLM_PROXY_URL`` (which
gptme passes to the Anthropic client). Paritok then receives native Anthropic
Messages-API requests on ``/v1/messages``:

.. code-block:: bash

    export ANTHROPIC_API_KEY=sk-ant-...
    LLM_PROXY_URL=http://127.0.0.1:8080/ \n        gptme --tool-format tool -m anthropic/claude-sonnet-4-5 'summarize the largest file'

.. note::

   ``LLM_PROXY_URL`` is global — it also redirects gptme's OpenAI/OpenRouter
   clients. Use it in a Claude-focused setup, or make sure the proxy can handle
   any other providers you call in the same session.

Verify
~~~~~~

.. code-block:: bash

    curl http://127.0.0.1:8080/stats     # live compressed-vs-original token totals

On a native-tool-call read of a ~600-line file, Paritok compresses the file body
by roughly 90% (e.g. ~6,700 tokens down to ~300), fully recallable on demand.

.. note::

   Paritok needs a Paritok-4B backend (local Ollama as above, or a hosted GPU
   endpoint via an API key). See the Paritok README for hosted-backend setup.

Backward compatibility
----------------------

The existing ``local`` provider continues to work using the ``OPENAI_BASE_URL``
and ``OPENAI_API_KEY`` environment variables. No changes are required for
existing configurations.

Related discussions
-------------------

Community threads and issues that motivated this page:

- `Ollama setup <https://github.com/gptme/gptme/discussions/177>`_
- `Llama 3.1 70B <https://github.com/gptme/gptme/discussions/178>`_
- `vLLM tokenizer timeouts <https://github.com/gptme/gptme/discussions/559>`_
- `Custom providers feature request <https://github.com/gptme/gptme/issues/673>`_
- `Requesty provider support <https://github.com/gptme/gptme/issues/514>`_
- `AI/ML provider support <https://github.com/gptme/gptme/issues/548>`_
- `Chutes provider support <https://github.com/gptme/gptme/issues/555>`_
