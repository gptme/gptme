:audience: power-user

Patch
=====

Single-file patches use the same staged replacement as :doc:`save`: failed
writes leave the original intact, permission bits and symlink targets are kept,
and hard-linked destinations are refused. The parent directory must be writable.
Replacement creates a new inode. As with save, extended metadata, inherited
default ACLs and ownership that cannot be retained cause a safe refusal instead
of silent metadata loss. No power-loss durability or concurrent-writer guarantee
is implied.

.. automodule:: gptme.tools.patch
    :members:
    :noindex:
