:audience: power-user

Subagent
========

.. automodule:: gptme.tools.subagent
    :members:
    :noindex:

Subagent Isolation Contract
---------------------------

When spawning a subagent you need to know exactly what it inherits from the parent
and what it starts fresh. There are four dimensions:

**1. Workspace config loading**

*Thread mode* (default, ``use_subprocess=False``):
  The subagent inherits the parent's *already-assembled* workspace context —
  the ``[prompt] files`` from ``gptme.toml`` and the ``context_cmd`` output as
  they were loaded for the parent session. It does **not** re-read from the
  subagent's working directory, so a subdirectory with its own ``gptme.toml``
  will not be picked up automatically.

*Subprocess mode* (``use_subprocess=True``):
  Spawns a fresh ``gptme`` process with ``workdir`` as the CWD, which naturally
  loads that directory's ``gptme.toml``. Use this when you want subagents to pick
  up directory-local workspace config.

Fine-grained control:

- ``context_mode="selective"`` + ``context_include`` — share only specific components
  (``"agent"``, ``"tools"``, ``"workspace"``) instead of the full workspace.

  Behavior by mode:

  **Thread**: fully supported — filters the inherited context to the specified components.

  **Subprocess**: ``context_mode`` is ignored (the child loads its own workspace from
  ``gptme.toml``); ``context_include=["workspace"]`` maps to the ``--context files``
  CLI flag to include workspace files. Other ``context_include`` values are ignored in
  this mode.

  **ACP**: both parameters are ignored.

- ``context_window=N`` — limit how many inherited context messages are forwarded
  (``0`` = none, ``None`` = all). Thread mode only; ignored in subprocess and ACP.

- ``context_turns=N`` — forward the last N turns of the parent conversation.
  Thread mode only; ignored in subprocess and ACP.

**2. Tool and state inheritance**

By default the subagent starts with the same tool list as the parent (both threads
share the same initial snapshot; contextvars are thread-isolated so the parent's
tool state cannot be mutated by the subagent).

Three ways to restrict tools:

- ``profile="explorer"`` (or any built-in profile) — applies a tool allowlist at spawn
  time. Built-in profiles: ``explorer`` (read-only), ``researcher``, ``developer``
  (full), ``verifier`` (read-only); see :doc:`../profiles`. Note: ``role="verify"`` forces
  ``use_subprocess=True`` and ``isolated=True`` in addition to the verifier profile.

- ``isolated=True`` — runs the subagent in a git worktree so filesystem writes don't
  affect the parent repo. The worktree is auto-cleaned after completion.

- ``redact_secrets=True`` (default) — scrubs common secret patterns (API keys, tokens,
  passwords) from workspace context messages before they reach the subagent.
  Thread-mode only; has no effect in subprocess or ACP modes (the child process's
  own ``gptme.toml`` controls its secret handling).

Signal tools are loaded regardless of allowlist so the subagent can communicate
back. Thread-mode subagents get ``complete``, ``clarify``, and ``progress``.
Subprocess subagents get ``complete`` and ``clarify``; ``progress`` is not loaded
because it depends on the parent's in-process notification queue.

**3. Cancellation and timeout**

- ``max_time`` (seconds) — a watchdog timer that marks the subagent result as
  ``"timeout"`` after the specified duration and delivers a timeout status
  notification. In subprocess mode the child process is terminated. In thread
  mode the background thread is not force-stopped; callers see the cached timeout
  result immediately while the thread continues until it finishes naturally.

- ``timeout`` (default 1800 s) — subprocess monitor kills the child process after
  this many seconds. Only applies in subprocess mode.

- The parent does not block waiting for subagents. Completion is delivered via the
  ``LOOP_CONTINUE`` hook, which re-enters the parent's loop with a notification
  message.

**4. Child transcript and result delivery**

Subagents always start with a **fresh conversation** — they do not inherit the parent's
message history by default. The result/transcript lifecycle:

- ``context_turns=N`` — the parent's last N turns are prepended to the subagent's
  conversation as context.

- On completion the subagent calls the ``complete`` signal tool with a summary; this
  is queued back to the parent via the ``LOOP_CONTINUE`` hook.

- ``subagent_read_log(agent_id)`` — retrieve the full child transcript from the parent
  after the subagent completes.

- ``subagent_status(agent_id)`` — poll completion/error state without waiting.

Fan-out and Parallel Execution
-------------------------------

Two helpers make it easy to run multiple independent tasks concurrently:

``subagent_parallel(tasks, ...)``
  Fan out N subagents in parallel and block until all complete. Returns results
  in the same order as the input tasks. Wall-clock time is bounded by the
  slowest agent, not their sum. Use this for straightforward parallel delegation
  where the parent needs all results before continuing::

      results = subagent_parallel([
          ("researcher", "Research async Python frameworks"),
          ("coder",      "Implement a basic async HTTP client"),
          ("tester",     "Write pytest tests for an async HTTP client"),
      ])

  Key parameters: ``isolated=True`` (each agent gets its own git worktree),
  ``output_schema`` (structured output — see below), ``model``, ``profile``,
  ``context_turns``, ``workdir``.

``subagent_batch(tasks, ...)``
  Non-blocking variant. Launches all subagents and returns a ``BatchJob``
  object immediately so the parent can continue working while agents run.
  Call ``job.wait_all()`` later to collect results. Useful when the parent
  has its own work to interleave::

      job = subagent_batch([
          ("a", "..."),
          ("b", "..."),
      ])
      # ... parent does other work ...
      results = job.wait_all()

``subagent_pipeline(items, *stages, ...)``
  Staged fan-out **without a barrier** between stages. Each item is processed
  through all stages in order, but items at different stages run concurrently —
  item A advances to stage 2 as soon as its stage-1 subagent completes, while
  item B may still be in stage 1. Wall-clock time is bounded by the slowest
  single-item chain, not the sum of the slowest per stage.

  This is more efficient than repeated ``subagent_parallel()`` calls (which add
  a full synchronisation barrier between stages) when items are independent.
  Each stage is a callable ``stage(item_prompt, prev_result) -> next_prompt``::

      items = [("auth", "Review auth.py"), ("db", "Review db.py")]
      results = subagent_pipeline(
          items,
          # Stage 0: review
          lambda item, _: f"Find bugs in this file: {item}",
          # Stage 1: verify — runs on auth while db is still in stage 0
          lambda item, prev: f"Adversarially verify these findings:\n{prev}",
      )
      # results[i][j] — result for item i at stage j
      for (prefix, _), stage_results in zip(items, results):
          print(f"{prefix}: {stage_results[-1]['result'][:80]}")

  Set ``isolated=True`` so concurrent file-editing subagents each get their own
  git worktree.

``subagent_wait_any(agent_ids, ...)``
  Return the first of the given subagents to complete. Useful for
  **speculative / hedging patterns**: spawn N subagents racing on the same
  task and take whichever finishes first, then cancel the rest::

      subagent("fast",     "Quick attempt at task X")
      subagent("thorough", "Thorough attempt at task X")
      first_id, result = subagent_wait_any(["fast", "thorough"], timeout=120)
      print(f"{first_id} won: {result['status']}")
      for aid in ("fast", "thorough"):
          if aid != first_id:
              subagent_cancel(aid)

  ``agent_ids`` is the list of IDs to wait on. Raises ``TimeoutError`` if no
  agent completes within ``timeout`` seconds (default 300).

Structured Output (output_schema)
----------------------------------

Both ``subagent_parallel()`` and ``subagent_batch()`` accept an
``output_schema`` parameter (a Pydantic model class). When set, each subagent
is instructed to return valid JSON matching the schema inside its ``complete``
block. Results are automatically parsed and validated — the ``"result"`` value
in each result dict is the parsed/validated object rather than a raw string::

    from pydantic import BaseModel

    class AnalysisResult(BaseModel):
        summary: str
        score: int
        issues: list[str]

    results = subagent_parallel(
        [("a1", "Analyze module A"), ("a2", "Analyze module B")],
        output_schema=AnalysisResult,
    )
    for r in results:
        if r["status"] == "success":
            analysis = r["result"]  # already a validated dict
            print(f"Score: {analysis['score']}")

The ``output_schema`` parameter is also available on the low-level
``subagent()`` call for single-agent structured output.

Token Budget Tracking
----------------------

``subagent_wait()`` and ``BatchJob.wait_all()`` include token usage in their
result dicts:

.. code-block:: python

    result = subagent_wait("my-agent")
    # result["input_tokens"]  — tokens consumed by the subagent's prompts
    # result["output_tokens"] — tokens generated by the subagent

This lets the parent track cumulative cost across a fleet of delegated tasks
and gate further spawning when a budget limit is reached.
