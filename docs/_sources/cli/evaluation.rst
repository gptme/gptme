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

.. click:: gptme.cli.cmd_dataset:dataset
   :prog: gptme-dataset
   :nested: full
