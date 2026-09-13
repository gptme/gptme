:audience: developer

API Reference
=============

Reference for gptme's Python API, generated from docstrings. It is most useful when
writing plugins, tools, or providers, or when embedding gptme in another program.
To drive gptme from scripts, :doc:`automation` is usually simpler.

- :doc:`api/core` — messages, code blocks, and conversation logs
- :doc:`api/llm` — LLM providers, model metadata, and model resolution
- :doc:`api/tools` — ``ToolSpec`` and tool loading
- :doc:`api/config` — user, project, and conversation configuration
- :doc:`api/plugins` — the ``GptmePlugin`` interface
- :doc:`api/commands` — slash-command registration
- :doc:`api/profiles` — agent profiles
- :doc:`api/memory` — the cross-harness memory store
- :doc:`api/prompts` — system prompt construction
- :doc:`api/server` — the gptme-server package

Hooks and context compression providers are documented alongside their guides: see
:ref:`hooks:API Reference` and :doc:`context-compression`.

.. toctree::
   :hidden:

   api/core
   api/llm
   api/tools
   api/config
   api/plugins
   api/commands
   api/profiles
   api/memory
   api/prompts
   api/server
