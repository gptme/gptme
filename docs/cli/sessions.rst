:audience: power-user

Sessions
========

Commands for handing off, resuming, checkpointing, and analyzing gptme sessions.

Transcript persistence
----------------------

Streamed text and server ``message_added`` events are provisional. The CLI
synchronizes the conversation before returning from a turn and before a
successful session exit, including messages from end hooks. The server emits
``generation_complete`` after turn hooks and the persistence barrier succeed.
An I/O error at that barrier fails completion instead of reporting success.

``LogManager.write(sync=True)`` synchronizes the written main, branch and view
transcripts, model selection trace, and existing recovery event logs. On POSIX,
it then synchronizes their directories, including newly created ancestors up to
the previously existing parent. That parent is assumed to have an established
durable namespace. Restart uses the same barrier. Ordinary appends remain
buffered by the operating system until a barrier; no maximum loss interval is
promised for an unfinished turn.

Transcript rewrites and event-log checkpoint compaction synchronize a temporary
file before atomic replacement and synchronize the containing directory after
replacement. This protects the previous transcript from interrupted in-place
truncation. These are per-file operations, not a transaction spanning all files.

The guarantee depends on the filesystem and storage honoring synchronization.
Tests exercise syscall ordering, I/O failures and isolated process termination;
they do not certify physical power-loss behavior. Windows lacks a portable
directory barrier in this implementation. Attachment snapshots, external ACP
runtime databases, and auxiliary session/skill ledgers have separate persistence
contracts and are not covered by this transcript acknowledgement.

.. click:: gptme.cli.cmd_status:status
   :prog: gptme-status
   :nested: full

.. click:: gptme.cli.cmd_resume:resume
   :prog: gptme-resume
   :nested: full

.. click:: gptme.cli.checkpoint:main
   :prog: gptme-checkpoint
   :nested: full

.. click:: gptme.cli.cmd_stats:stats
   :prog: gptme-stats
   :nested: full
