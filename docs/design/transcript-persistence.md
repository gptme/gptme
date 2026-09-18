---
audience: developer
---

# Transcript Persistence

**Status:** implemented.

What gptme promises about a conversation transcript surviving a crash, and what
it deliberately does not promise.

## The problem

Every `LogManager.append()` writes the transcript, but a write only reaches the
operating system's page cache. Data in the page cache survives a process crash;
it does not survive a kernel panic or power loss until writeback happens, which
can be seconds later. Both entry points used to report success before that:
the CLI returned from a turn, and the server emitted `generation_complete`,
while the turn was still only provisionally on disk.

Two related defects made that worse than a timing window:

- Transcript rewrites (fork, undo, edit) truncated the file in place. A crash
  mid-rewrite left a partially written conversation with no recoverable copy.
- `LogManager.logfile` resolved the main transcript through
  `get_logs_dir() / chat_id` instead of the manager's own `logdir`, so a session
  with a custom log directory wrote its main transcript somewhere else.

## The barrier

`LogManager.write(sync=True)` syncs the written main, branch and view
transcripts, the model-selection trace, and existing recovery event logs. On
POSIX it then syncs their directories, including newly created ancestors up to
the parent that already existed when the manager was created; that parent is
assumed to have an established durable namespace.

Callers:

- **CLI** — before returning from a turn and before a successful session exit,
  in both cases after end-of-turn and session-end hooks have contributed their
  messages. Restart uses the same barrier.
- **Server** — after `generation_complete`, then followed by `turn_persisted`.
- **Rewrites** — transcript rewrites and event-log checkpoint compaction sync a
  temporary file, replace atomically, then sync the containing directory. A
  symlinked transcript is replaced through the link, not in place of it.

Ordinary appends stay buffered by the operating system until a barrier. No
maximum loss interval is promised for an unfinished turn.

## Two server events, not one

`generation_complete` is emitted as soon as the assistant message has been
appended, before turn hooks and before the barrier. It means *generation
finished*, and it is what a rendering client should wait for: making it wait on
arbitrary hook code and an `fsync` would be a visible latency regression for no
benefit to the thing it drives.

`turn_persisted` follows the barrier and is the durability acknowledgement.
Clients that must not lose a turn (handoff, checkpointing, external ledgers)
wait for that one. A barrier failure produces an `error` event and a visible
system message in the transcript instead of a silent acknowledgement.

## What this does not cover

- **Power loss.** Tests exercise syscall ordering, I/O failures and isolated
  process termination. They do not certify physical power-loss behavior, which
  depends on the filesystem and the drive honoring the barrier.
- **Filesystems without a directory barrier.** Some network, overlay and FUSE
  mounts reject `fsync` on a directory. gptme warns once per directory and
  continues: a weaker namespace guarantee is not a reason to fail a turn whose
  contents were written and synced. Genuine I/O errors still propagate.
- **Windows.** Python exposes no portable directory barrier. File `fsync` still
  applies; namespace durability does not.
- **Atomicity across files.** Each file is replaced atomically. A barrier is not
  a transaction spanning all of a session's artifacts.
- **Other artifacts.** Attachment snapshots, external ACP runtime databases, and
  auxiliary session/skill ledgers have their own persistence contracts.
