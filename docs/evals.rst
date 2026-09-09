Evals
=====

gptme provides LLMs with a wide variety of tools, but how well do models make use of them? Which tasks can they complete, and which ones do they struggle with? How far can they get on their own, without any human intervention?

To answer these questions, we have created an evaluation suite that tests the capabilities of LLMs on a wide variety of tasks.

The suite covers fundamental tool use, web browsing, project initialization, and a growing set of **practical programming tasks** that reflect real-world agentic work: building APIs, refactoring code, parsing data formats, writing tests, and more.

Recommended Model
-----------------

The recommended model for API-key use is **Claude Sonnet 4.6** (``anthropic/claude-sonnet-4-6``, or ``openrouter/anthropic/claude-sonnet-4-6``) for its:

- Strong agentic capabilities
- Strong coder capabilities
- Strong performance across all tool types and formats
- Reasoning capabilities
- Vision & computer use capabilities

If you already pay for a frontier subscription, use it instead of an API key (see `Subscriptions`_ below): **GPT-6 Astra** via ChatGPT Plus/Pro (``openai-subscription/gpt-6-astra``) and **Grok 4.6** via SuperGrok (``grok-subscription/grok-4.6``) are both frontier-class and cost nothing per token.

For high-volume or cost-sensitive work, two open-weight "flash" models hold up well in agentic use for a small fraction of the price:

- **DeepSeek V4 Flash** (the 2026-07-31 release: ``openrouter/deepseek/deepseek-v4-flash-0731``, or ``deepseek/deepseek-v4-flash`` on the official API)
- **GLM 5.3 Flash** (``openrouter/z-ai/glm-5.3-flash``)

Both are hosted by many providers on OpenRouter with very different reliability, speed, and data policies; see `Choosing an OpenRouter provider`_ before picking one.

Default model per provider
~~~~~~~~~~~~~~~~~~~~~~~~~~

This table is generated at docs-build time from the installed gptme, so it always reflects what ``gptme -m <provider>`` (provider name, no model) resolves to in the current release. The summary model is the cheaper model used for conversation titles and summaries.

.. command-output:: gptme-util models recommended
   :cwd: ..
   :shell:

Decent alternatives include:

- GPT-5.6 Sol / Terra / Luna (``openai/gpt-5.6-sol``, ``openai-subscription/gpt-5.6-sol``)
- Gemini 3.1 Pro (``gemini/gemini-3.1-pro-preview``, ``openrouter/google/gemini-3-pro-preview``)
- Grok 4.6 via the API (``xai/grok-4.6``, ``openrouter/x-ai/grok-4.6``)
- DeepSeek V4 Pro (``openrouter/deepseek/deepseek-v4-pro-0813``, ``deepseek/deepseek-v4-pro``)
- Kimi K3 / K2.6 (``moonshot/kimi-k3``, ``openrouter/moonshotai/kimi-k2.6``)
- Qwen3 Max (``openrouter/qwen/qwen3-max``)
- MiniMax M2 (``openrouter/minimax/minimax-m2``)

Note that some models may perform better or worse with different ``--tool-format`` options (``markdown``, ``xml``, or ``tool`` for native tool-calling). See :doc:`tool-formats` for what each format does and how to choose one.

You can get an overview of actual model usage in the wild from the `OpenRouter app analytics for gptme <https://openrouter.ai/apps?url=https://github.com/gptme/gptme>`_.

Choosing an OpenRouter provider
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Open-weight models are served by many providers on OpenRouter, and the provider matters as much as the model: quality (quantization), latency, uptime, cache pricing, and data policy all differ. Pin a provider with ``model@provider``, or an ordered allowlist with ``model@a,b`` (falls back within the list on rate limits), and set ``OPENROUTER_PROVIDER_ORDER`` to apply a default allowlist to every request. See :doc:`providers` for details.

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

Subscriptions
~~~~~~~~~~~~~

Several frontier models are reachable through a consumer subscription instead of a metered API key, which is usually the cheapest way to run gptme on a frontier model:

- **ChatGPT Plus/Pro (Codex)** — ``openai-subscription/gpt-6-astra`` (and the GPT-5.6 family). Authenticate once with ``gptme-auth openai-subscription``.
- **SuperGrok (Grok Build)** — ``grok-subscription/grok-4.6``. Reuses the grok CLI's login, or ``gptme-auth grok-subscription``.
- **Claude Max** — not available: Anthropic does not permit third-party tools on the consumer subscription; use the ``anthropic`` provider with an API key.
- **Cursor** — not supported. Cursor exposes no sanctioned endpoint for using a personal subscription from third-party tools (its Cloud Agents API is separately metered and is not a chat-completions API). Unofficial proxies that reuse the Cursor CLI's login state exist, but gptme does not integrate them.

See :doc:`providers` for setup details.

Model Leaderboard
-----------------

The table below shows pass rates across our eval suites for each model (best tool format per model). Models are ranked by overall pass rate, with breakdowns by suite type.

.. command-output:: python -m gptme.eval.leaderboard --results-dir eval_results --format rst --min-tests 4
   :cwd: ..
   :shell:

**Notes:**

- *Format* shows the best-performing ``--tool-format`` for each model (see :doc:`tool-formats`).
- *Basic* tests cover fundamental tool use (file I/O, shell, git, Python).
- *Practical* tests cover real-world programming tasks (APIs, data processing, refactoring).
- Models with fewer than 4 tests are excluded.
- Results use a 300-second timeout per test. Some models may perform better with longer timeouts.

To generate this table locally:

.. code-block:: bash

    gptme-eval --leaderboard --leaderboard-format rst
    gptme-eval --leaderboard --leaderboard-format csv       # for data analysis
    gptme-eval --leaderboard --leaderboard-format markdown   # for GitHub/blog
    gptme-eval --leaderboard --leaderboard-format html       # self-contained HTML page


Usage
-----

You can run the simple ``hello`` eval like this:

.. code-block:: bash

    gptme-eval hello --model anthropic/claude-sonnet-4-6

However, we recommend running it in Docker to improve isolation and reproducibility:

.. code-block:: bash

    make build-docker
    docker run \
        -e "ANTHROPIC_API_KEY=<your api key>" \
        -v $(pwd)/eval_results:/app/eval_results \
        gptme-eval hello --model anthropic/claude-sonnet-4-6

Available Eval Suites
---------------------

The evaluation suite is organized into named suites that can be run individually or together:

**basic**
  Fundamental tool use: reading and writing files, patching code, running Python in IPython,
  executing shell commands, using git, counting words, transforming JSON, multi-file refactoring,
  writing tests, generating CLI programs, and fixing bugs. (~18 tests)

**browser**
  Web browsing and data extraction using the browser tool.

**init_projects**
  Project initialization: ``init-git``, ``init-react``, ``init-rust``. Tests the ability
  to scaffold new projects from scratch.

**practical** — **practical2** — ... — **practical33**
  A growing series of real-world programming tasks that go beyond basic file I/O.
  The practical suites now cover 99 tasks across data processing, refactoring,
  algorithms, async/concurrency, SQL, validation, graph search, dynamic
  programming, tree data structures, and classic interview problems.

  Early suites give a good feel for the format:

  +------------+------------------------------------------+----------------------------------+
  | Suite      | Description                              | Tests                            |
  +============+==========================================+==================================+
  | practical  | Web APIs, log parsing, error handling    | build-api, parse-log,            |
  |            |                                          | add-error-handling               |
  +------------+------------------------------------------+----------------------------------+
  | practical2 | Data filtering, templating, CSV          | sort-and-filter, template-fill,  |
  |            | validation                               | validate-csv                     |
  +------------+------------------------------------------+----------------------------------+
  | practical3 | Unit test writing, SQLite                | write-tests-calculator,          |
  |            | persistence                              | sqlite-store                     |
  +------------+------------------------------------------+----------------------------------+
  | practical4 | Data aggregation, schedule overlap       | group-by, schedule-overlaps,     |
  |            | detection, topological sort              | topo-sort                        |
  +------------+------------------------------------------+----------------------------------+
  | practical5 | Code refactoring, data pipelines,        | rename-function, data-pipeline,  |
  |            | regex scrubbing                          | regex-scrub                      |
  +------------+------------------------------------------+----------------------------------+
  | practical6 | CSV analysis, word frequency             | csv-analysis, word-frequency,    |
  |            | counting, config merging                 | merge-configs                    |
  +------------+------------------------------------------+----------------------------------+
  | practical7 | INI-to-JSON conversion, JSON diff,       | ini-to-json, json-diff,          |
  |            | changelog generation                     | changelog-gen                    |
  +------------+------------------------------------------+----------------------------------+

  Later suites extend coverage with semver sorting, Roman numerals, matrix and
  bracket tasks, async pipelines and worker queues, SQL analytics, tries,
  LRU caches, interval merging, min-stack, knight moves, histogram area,
  edit distance, BST operations, coin change, Dijkstra, spiral matrix,
  number of islands, Kadane's algorithm, 0/1 knapsack, flood fill,
  trapping rain water, word break, permutations, longest common subsequence,
  stock trading with cooldown, image rotation, N-Queens, longest increasing
  subsequence, cycle detection, sliding window maximum, decode ways,
  meeting rooms, longest palindromic substring, jump game, task scheduler,
  house robber, max product subarray, finding all anagrams, minimum path sum,
  gas station, next permutation, word break II, unique paths, rotate array,
  decode string, top-k frequent elements, partition equal subset sum,
  3sum, majority element (Boyer-Moore voting), counting bits, combination sum,
  generate parentheses, single number (XOR), product except self,
  find duplicate (Floyd's cycle detection), and missing number.

  For the current authoritative suite list, run ``gptme-eval --list``.

Run specific tests or suites by name:

.. code-block:: bash

    gptme-eval build-api --model anthropic/claude-sonnet-4-6
    gptme-eval sort-and-filter rename-function --model anthropic/claude-sonnet-4-6

Run all practical suites at once (useful for benchmarking):

.. code-block:: bash

    gptme-eval all-practical --model anthropic/claude-sonnet-4-6

    # Or run every suite (basic + browser + init_projects + practical):
    gptme-eval all --model anthropic/claude-sonnet-4-6


Raw Results
-----------

Full per-test results from all eval runs are stored as CSV files in ``eval_results/`` subdirectories.
Results are published to the ``eval-results`` branch of the repository.

To view raw results locally:

.. code-block:: bash

    # View latest results
    cat eval_results/*/eval_results.csv | head -50

    # Export leaderboard as CSV for analysis
    gptme-eval --leaderboard --leaderboard-format csv

    # Export as JSON for programmatic use
    gptme-eval --leaderboard --leaderboard-format json


Other evals
-----------

SWE-Bench support is now available via ``gptme-eval-swebench``.
It can:

- inspect datasets and instances with ``--info``
- generate ``predictions.jsonl`` in the official SWE-Bench format
- resume interrupted runs with ``--resume``
- optionally invoke the official harness with ``--run-harness``

Example single-instance smoke test:

.. code-block:: bash

    gptme-eval-swebench \
        -m anthropic/claude-sonnet-4-6 \
        -i django__django-11099

Example full SWE-Bench Lite run:

.. code-block:: bash

    gptme-eval-swebench \
        -m anthropic/claude-sonnet-4-6 \
        --resume \
        --run-harness \
        --dataset princeton-nlp/SWE-bench_Lite \
        --run-id gptme_baseline_2026

Notes:

- The built-in summary printed by ``gptme-eval-swebench`` is a lightweight file-coverage heuristic.
  For authoritative pass/fail results and leaderboard submission, use the official SWE-Bench harness.

- ``--run-harness`` requires Docker plus ``swebench[evaluation]`` dependencies.

- Use ``gptme-eval-swebench --info`` to inspect dataset size and specific instance IDs before launching an expensive run.

See also:

- `PR #1994 <https://github.com/gptme/gptme/pull/1994>`_ — SWE-Bench harness integration
- `PR #2045 <https://github.com/gptme/gptme/pull/2045>`_ — resume support for interrupted runs
