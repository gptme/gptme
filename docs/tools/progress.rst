:audience: power-user

Progress
========

The ``progress`` tool lets a subagent send an intermediate status update to its
parent orchestrator without stopping the session — the counterpart to
``complete`` (ends the session) and ``clarify`` (pauses it). The update arrives
as a ⏳ system message via the parent's ``LOOP_CONTINUE`` hook.

The tool is opt-in and enabled automatically for subagents; enable it explicitly
with ``-t +progress``.

.. automodule:: gptme.tools.progress
    :members:
    :noindex:
