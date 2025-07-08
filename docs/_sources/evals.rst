Evals
=====

gptme provides LLMs with a wide variety of tools, but how well do models make use of them? Which tasks can they complete, and which ones do they struggle with? How far can they get on their own, without any human intervention?

To answer these questions, we have created an evaluation suite that tests the capabilities of LLMs on a wide variety of tasks.

.. note::
    The evaluation suite is still tiny and under development, but the eval harness is fully functional.

Recommended Model
-----------------

The recommended model is **Claude Sonnet 4** (``anthropic/claude-sonnet-4-20250514`` and ``openrouter/anthropic/claude-sonnet-4``) for its:

- Strong agentic capabilities
- Strong coder capabilities
- Strong performance across all tool types and formats
- Reasoning capabilities
- Vision & computer use capabilities

Decent alternatives include:

- GPT-4o (``openai/gpt-4o``)
- Llama 3.1 405B (``openrouter/meta-llama/llama-3.1-405b-instruct``)
- DeepSeek V3 (``deepseek/deepseek-chat``)
- DeepSeek R1 (``deepseek/deepseek-reasoner``)

Usage
-----

You can run the simple ``hello`` eval with Claude 3.7 Sonnet like this:

.. code-block:: bash

    gptme-eval hello --model anthropic/claude-sonnet-4-20250514

However, we recommend running it in Docker to improve isolation and reproducibility:

.. code-block:: bash

    make build-docker
    docker run \
        -e "ANTHROPIC_API_KEY=<your api key>" \
        -v $(pwd)/eval_results:/app/eval_results \
        gptme-eval hello --model anthropic/claude-sonnet-4-20250514

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
