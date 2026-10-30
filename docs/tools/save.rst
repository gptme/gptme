:audience: power-user

Save
====

Save stages a complete replacement beside the destination before publishing it.
A failed write or close leaves the original intact. Existing permission bits and
symlink targets are preserved; hard-linked destinations are refused rather than
silently splitting aliases. Replacement requires a writable parent directory.
Replacement creates a new inode. Files with extended metadata (including POSIX
ACLs), inherited default ACLs on an existing target's parent, or ownership that
cannot be retained are refused rather than silently losing security metadata.
This is not a power-loss durability or concurrent-writer guarantee. Append
retains its separate in-place behavior.

.. automodule:: gptme.tools.save
    :members:
    :noindex:
