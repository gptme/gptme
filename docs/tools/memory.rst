:audience: power-user

Memory
======

The ``memory`` tool saves a fact to the shared cross-runtime memory store, so a
later session on any runtime (gptme, Claude Code, Codex) can recall it. Entries
are Markdown files under the memory root alongside an index line in
``MEMORY.md``, which every session loads at startup.

The first line of the block is the one-line summary shown in the index; the rest
is the body:

.. code-block:: text

    memory prefer-short-answers
    User prefers short, direct answers without preamble.

.. automodule:: gptme.tools.memory
    :members:
    :noindex:
