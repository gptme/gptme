Evals
=====

gptme provides LLMs with a wide variety of tools, but how well do models make use of them? Which tasks can they complete, and which ones do they struggle with? How far can they get on their own, without any human intervention?

To answer these questions, we have created an evaluation suite that tests the capabilities of LLMs on a wide variety of tasks.

The suite covers fundamental tool use, web browsing, project initialization, and a growing set of **practical programming tasks** that reflect real-world agentic work: building APIs, refactoring code, parsing data formats, writing tests, and more.

Recommended Model
-----------------

The recommended model is **Claude Sonnet 4.6** (``anthropic/claude-sonnet-4-6`` and ``openrouter/anthropic/claude-sonnet-4-6``) for its:

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


Model Leaderboard
-----------------

The table below shows pass rates across our eval suites for each model (best tool format per model). Models are ranked by overall pass rate, with breakdowns by suite type.

.. command-output:: python -m gptme.eval.leaderboard --results-dir eval_results --format rst --min-tests 4
   :cwd: ..
   :shell:

**Notes:**

- *Format* shows the best-performing ``--tool-format`` for each model.
- *Basic* tests cover fundamental tool use (file I/O, shell, git, Python).
- *Practical* tests cover real-world programming tasks (APIs, data processing, refactoring).
- Models with fewer than 4 tests are excluded.
- Results use a 300-second timeout per test. Some models may perform better with longer timeouts.

To generate this table locally:

.. code-block:: bash

    gptme eval --leaderboard --leaderboard-format rst
    gptme eval --leaderboard --leaderboard-format csv       # for data analysis
    gptme eval --leaderboard --leaderboard-format markdown   # for GitHub/blog
    gptme eval --leaderboard --leaderboard-format html       # self-contained HTML page


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

**practical** — **practical2** — ... — **practical29**
  A growing series of real-world programming tasks that go beyond basic file I/O.
  The practical suites now cover 86 tasks across data processing, refactoring,
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
  gas station, next permutation, word break II, unique paths, and rotate array.

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
    gptme eval --leaderboard --leaderboard-format csv

    # Export as JSON for programmatic use
    gptme eval --leaderboard --leaderboard-format json


Other evals
-----------

We have considered running gptme on other evals such as SWE-Bench, but have not finished it (see `PR #142 <https://github.com/gptme/gptme/pull/142>`_).

If you are interested in running gptme on other evals, drop a comment in the issues!
