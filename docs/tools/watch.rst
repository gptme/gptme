:audience: power-user

Watch
=====

The ``watch`` tool arms an event source and returns immediately; gptme keeps
working and delivers one system message when the event fires. Use it instead of
polling with ``sleep N; check`` — for example, to wait for CI without blocking:

.. code-block:: text

    watch
    until gh pr checks 42 --repo gptme/gptme --every 60s --timeout 30m

Sources include ``run``, ``until``, ``stream``, ``timer`` and ``tmux``, with
``list``, ``cancel`` and ``wait`` to manage them. The tool is opt-in
(``disabled_by_default``); enable it with ``-t +watch``.

.. automodule:: gptme.tools.watch
    :members:
    :noindex:
