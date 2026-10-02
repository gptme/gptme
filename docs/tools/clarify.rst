:audience: power-user

Clarify
=======

The ``clarify`` tool lets a subagent pause and ask its parent orchestrator a
question when it cannot proceed without more information. The session stops
cleanly and the parent can answer with ``subagent_reply(agent_id, reply)``,
which re-spawns the subagent with the original task and the Q&A in context.

The tool is opt-in and enabled automatically for subagents; enable it explicitly
with ``-t +clarify``.

.. automodule:: gptme.tools.clarify
    :members:
    :noindex:
