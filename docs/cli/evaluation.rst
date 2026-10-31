:audience: power-user

Evaluation
==========

Commands for running gptme's eval suites and benchmarks, and for building
fine-tuning datasets from session trajectories. See :doc:`../evals` and
:doc:`../finetuning`.

.. click:: gptme.eval.main:main
   :prog: gptme-eval
   :nested: full

.. click:: gptme.eval.swebench.main:main
   :prog: gptme-eval-swebench
   :nested: full

.. click:: gptme.eval.tbench.run:main
   :prog: gptme-eval-tbench
   :nested: full

Dataset history eligibility
---------------------------

``gptme-util dataset export`` emits an entry commit plus the session-attributed
solution commits. Export and ``dataset stats`` count only contiguous,
single-parent solution chains with an available entry parent. Earlier foreign
commits are part of the starting environment, not part of the solution.

Root solutions, attributed merges, interleaved solutions (A/B/A), and nonlinear
attributed histories are skipped with a warning naming the session and reason.
Even apparently independent intervening work is rejected: the format does not
model missing dependencies or a merge mainline. Shallow histories without a
visible entry parent are also skipped. Fetch the necessary history or select a
repository with a supported chain; foreign commits are never silently added.

This checks structural Git replay, not task correctness, build success, or the
availability of external dependencies. JSONL records and their schema are
unchanged; diagnostics go to stderr rather than the exported JSONL stream.

.. click:: gptme.cli.cmd_dataset:dataset
   :prog: gptme-dataset
   :nested: full
