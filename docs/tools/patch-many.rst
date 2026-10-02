:audience: power-user

Patch many
==========

The ``patch_many`` tool applies several ``patch``-style changes in one call and
commits them atomically: if any hunk fails validation, no file is written. It is
meant for cross-cutting edits ("rename this symbol", "add a parameter and update
its callers") that would otherwise take one ``patch`` call per file.

Patches are validated in-memory before anything lands, so a half-applied
refactor is impossible.

.. automodule:: gptme.tools.patch_many
    :members:
    :noindex:
