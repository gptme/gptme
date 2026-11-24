:audience: power-user

Save
====

Save stages a complete replacement beside the destination before publishing it.
A failed write or close leaves the original intact. Existing permission bits and
symlink targets are preserved; hard-linked destinations are refused rather than
silently splitting aliases. Replacement requires a writable parent directory.
On POSIX, staging requires a parent owned by the current user with no group or
other write bits (including sticky shared directories); otherwise another user
could substitute the staging name. This check applies to new files too. It does
not protect against same-user concurrent writers or hostile ancestor changes.
Replacement creates a new inode. Files with extended metadata (including POSIX
ACLs), inherited default ACLs on an existing target's parent, or ownership that
cannot be retained are refused rather than silently losing security metadata.
On Windows and macOS an existing file is rewritten in place on its current
inode instead of being replaced: Python's permission-bit APIs cannot preserve a
Windows security descriptor (and staging under the parent's inherited DACL could
expose restricted contents), and macOS ACLs are not reliably visible through
``listxattr()``. Writing in place keeps the security descriptor, ACLs, extended
attributes and hard-link aliases. The content is encoded before the file is
opened, so an encoding error cannot truncate it; if writing fails partway (for
example a full disk), the original bytes are written back before the error is
reported. That restore is best effort: a failure that also prevents it can leave
the file damaged. New files are staged and published as on other platforms.
On Linux, a filesystem reporting unsupported extended attributes is treated as
having none; permission and I/O errors during inspection still fail closed.
Platforms without an extended-attribute inspection API refuse existing-file
replacement. This is not a power-loss durability or concurrent-writer guarantee.
Append retains its separate in-place behavior.

.. automodule:: gptme.tools.save
    :members:
    :noindex:
