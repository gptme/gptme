:audience: power-user

Request tool change
===================

``request_tool_change`` is an opt-in, audit-only relief valve for assistants
that need a different session tool configuration. It records a structured
request in ordinary tool-call history; it does not enable, disable, or configure
any tool. Enable it explicitly with ``-t +request_tool_change``.

This is separate from ``vent``: ``vent`` records operational friction, while
``request_tool_change`` names a concrete tool-configuration request.

.. automodule:: gptme.tools.request_tool_change
    :members:
    :noindex:
