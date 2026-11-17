:audience: power-user

Tmux
====

.. automodule:: gptme.tools.tmux
    :members:
    :noindex:

Guardrail path context
---------------------

With guardrails enabled, literal shell text in ``new-session`` is checked
relative to the process directory used to start the shell. For ``send-keys``,
the guard queries the target pane's current directory, including session
shorthand and native pane IDs, before checking relative operands and search
exclusion files. An unavailable pane leaves only the existing process-relative
checks; it does not provide verified pane context.

These checks are heuristics, not a sandbox. The directory query is a snapshot;
a preceding ``cd``, concurrent pane input, shell startup files, or dynamically
generated commands can change the eventual interpretation of the typed text.
