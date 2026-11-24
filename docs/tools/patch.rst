:audience: power-user

Patch
=====

Single-file patches use the same staged replacement as :doc:`save`: failed
writes leave the original intact, permission bits and symlink targets are kept,
and hard-linked destinations are refused. The parent directory must be writable.
POSIX staging also requires that parent to be owned by the current user with no
group or other write bits, including sticky shared directories, so another user
cannot substitute the staging name. Same-user writers and hostile ancestor
changes remain outside this guarantee.
Replacement creates a new inode. As with save, extended metadata, inherited
default ACLs and ownership that cannot be retained cause a safe refusal instead
of silent metadata loss. On Windows and macOS, where a replacement inode could
drop the security descriptor or ACLs, an existing file is rewritten in place
instead, as described in :doc:`save`. Platforms without extended-attribute
inspection fail closed too. Linux filesystems that report unsupported extended
attributes remain supported; other metadata inspection errors still refuse the
write. No power-loss durability or concurrent-writer guarantee is implied.

.. automodule:: gptme.tools.patch
    :members:
    :noindex:
