Evals
=====

gptme provides LLMs with a wide variety of tools, but how well do models make use of them? Which tasks can they complete, and which ones do they struggle with? How far can they get on their own, without any human intervention?

To answer these questions, we have created an evaluation suite that tests the capabilities of LLMs on a wide variety of tasks.

.. note::
    The evaluation suite is still tiny and under development, but the eval harness is fully functional.

Recommended Model
-----------------

The recommended model is **Claude Sonnet 4.5** (``anthropic/claude-sonnet-4-5`` and ``openrouter/anthropic/claude-sonnet-4-5``) for its:

- Strong agentic capabilities
- Strong coder capabilities
- Strong performance across all tool types and formats
- Reasoning capabilities
- Vision & computer use capabilities

Decent alternatives include:

- Gemini 3 Pro (``openrouter/google/gemini-3-pro-preview``, ``gemini/gemini-3-pro-preview``)
- GPT-5, GPT-4o (``openai/gpt-5``, ``openai/gpt-4o``)
- Grok 4 (``xai/grok-4``, ``openrouter/x-ai/grok-4``)
- Qwen3 Coder 480B A35B (``openrouter/qwen/qwen3-coder``)
- Kimi K2 (``openrouter/moondreamai/kimi-k2-thinking``, ``openrouter/moondreamai/kimi-k2``)
- MiniMax M2 (``openrouter/minimax/minimax-m2``)
- Llama 3.1 405B (``openrouter/meta-llama/llama-3.1-405b-instruct``)
- DeepSeek V3 (``deepseek/deepseek-chat``)
- DeepSeek R1 (``deepseek/deepseek-reasoner``)

Note that some models may perform better or worse with different ``--tool-format`` options (``markdown``, ``xml``, or ``tool`` for native tool-calling).

Note that many providers on OpenRouter have poor performance and reliability, so be sure to test your chosen model/provider combination before committing to it. This is especially true for open weight models which any provider can host at any quality. You can choose a specific provider by appending with ``:provider``, e.g. ``openrouter/qwen/qwen3-coder:alibaba/opensource``.

Note that pricing for models varies widely when accounting for caching, making some providers much cheaper than others. Anthropic is known and tested to cache well, significantly reducing costs for conversations with many turns.

You can get an overview of actual model usage in the wild from the `OpenRouter app analytics for gptme <https://openrouter.ai/apps?url=https://github.com/gptme/gptme>`_.

Usage
-----

You can run the simple ``hello`` eval like this:

.. code-block:: bash

    gptme-eval hello --model anthropic/claude-sonnet-4-5

However, we recommend running it in Docker to improve isolation and reproducibility:

.. code-block:: bash

    make build-docker
    docker run \
        -e "ANTHROPIC_API_KEY=<your api key>" \
        -v $(pwd)/eval_results:/app/eval_results \
        gptme-eval hello --model anthropic/claude-sonnet-4-5

Available Evals
---------------

The current evaluations test basic tool use in gptme, such as the ability to: read, write, patch files; run code in ipython, commands in the shell; use git and create new projects with npm and cargo. It also has basic tests for web browsing and data extraction.

.. This is where we want to get to:

    The evaluation suite tests models on:

    1. Tool Usage

       - Shell commands and file operations
       - Git operations
       - Web browsing and data extraction
       - Project navigation and understanding

    2. Programming Tasks

       - Code completion and generation
       - Bug fixing and debugging
       - Documentation writing
       - Test creation

    3. Reasoning

       - Multi-step problem solving
       - Tool selection and sequencing
       - Error handling and recovery
       - Self-correction


Results
-------

Here are the results of the evals we have run so far:

.. command-output:: gptme-eval eval_results/*/eval_results.csv
   :cwd: ..
   :shell:

We are working on making the evals more robust, informative, and challenging.


Other evals
-----------

We have considered running gptme on other evals such as SWE-Bench, but have not finished it (see `PR #142 <https://github.com/gptme/gptme/pull/142>`_).

If you are interested in running gptme on other evals, drop a comment in the issues!
