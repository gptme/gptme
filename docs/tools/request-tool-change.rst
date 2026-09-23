:audience: power-user

Request tool change
===================

``request_tool_change`` is an opt-in relief valve for assistants that need a
different session tool configuration. Every request is recorded in ordinary
tool-call history, and ``enable_tool`` and ``disable_tool`` requests are applied
to the current session: the tool is loaded or unloaded immediately. Enable the
tool explicitly with ``-t +request_tool_change``.

Enabling is still bounded by the session's tool allowlist — a request for a tool
outside ``--tools`` is refused and recorded, so this is not a way around the
tools you granted. ``configure_tool`` requests remain audit-only: they are
recorded but never applied.

This is separate from ``vent``: ``vent`` records operational friction, while
``request_tool_change`` names a concrete tool-configuration request.

.. automodule:: gptme.tools.request_tool_change
    :members:
    :noindex:
