---
audience: developer
---

# Process-per-Conversation Server Workers and Cross-Process Wake Delivery

**Status:** research note, partial. Tracks [gptme/gptme#5060](https://github.com/gptme/gptme/issues/5060).
Not an implementation commitment. Independent short-term safety work stays in #5059.

**Recommendation: stage, do not adopt yet, and do not make the kernel backend the
server default.** Details and the evidence for each claim follow. Everything marked
*measured* comes from `scripts/bench_conversation_workers.py` (commands at the end);
everything marked *audited* comes from reading the named code at `8a09fedffe`.

## 1. Problem

Native server conversations are threads in one process. Anything that touches the
process-global cwd — `open("rel")`, `Path.exists`, `subprocess.run` without `cwd=`,
native extensions calling `getcwd(3)` — is wrong for all but one conversation unless
every tool and plugin routes through `current_workspace()` / `resolve_in_workspace()`
(`gptme/tools/base.py:1651`). `tools/python.py` papers over the in-process IPython
case with a global `_inprocess_cwd_lock` plus a temporary `os.chdir`
(`gptme/tools/python.py:93-320`); threads outside that lock still observe the
temporary cwd (#4951, #5051).

*Measured:* two threads in one process, each `os.chdir`-ing to its own workspace and
doing an unmodified `open("marker.txt")`, read the **wrong** workspace's file in
**332 of 4000** attempts. Two worker processes running the same four probes
(`open(relative)`, `Path.exists`, `subprocess.run(["cat", ...])` with no `cwd`, and a
`ctypes` call to libc `getcwd`) were correct in all of them.

## 2. Ownership map (audited)

| Unit | Owner today | Evidence |
|---|---|---|
| Conversation (`logdir`) | one **writer** at a time; the lifetime lock `.lock` is held for the writer's lifetime | `request_peer_wake` takes `LogManager.load(lock=True)` and pins `writer_manager`; peer discovery proves liveness by lock contention (`peers.py::_active_conversations`) |
| Browser / SSE session | an **observer**, not a writer. Several `ConversationSession`s may exist per conversation | `get_sessions_for_conversation`; wake paths pick `max(sessions, key=last_activity)` |
| Step admission | **per conversation**, not per branch: `_conversation_busy` ORs generating, active command, pending/executing tools over *all* sessions | `session_models.py:486` |
| Branch | a log view; wakes pass `branch=` through to the log append, but admission above is conversation-wide | `request_watch_wake`, `request_peer_wake` |

**Decision for a worker design:** one worker per *conversation*, one active step per
worker, branches scheduled sequentially inside it. That preserves today's concurrency
(it is already conversation-serial) and makes "multiple observers must not create
duplicate writers" structural: observers attach to the *host's* HTTP/SSE layer, only the
worker holds the log lease. Distinct runtimes per branch (e.g. a branch with a different
workspace) are not supported today and would need either a worker per `(conversation,
branch)` or explicit branch switching with state loss; I did not find a current feature
that requires it, so this is left as an open question for the issue thread.

## 3. Options compared (measured)

Environment: CPython 3.12.3, Linux 7.0.6 (LXC, 24 cores, 30 GiB), uv-installed gptme
with the `kernel` extra. Linux only; **macOS/Windows not measured** (see gaps).
Worker preload = `gptme.chat` + `gptme.server.session_step` imports, **not** a full
`init_tools()` + live LLM turn, so memory is a lower bound.

### 3.1 Startup and memory

| Mode | Cold → ready (p50) | RSS | PSS |
|---|---|---|---|
| Thread in host | 0.02 ms | — (shared) | — (shared) |
| Bare `python` worker (spawn) | 26 ms | 14.9 MB | 9.2 MB |
| gptme-preloaded worker (spawn) | **216 ms** (max 684) | 50.4 MB | **41.8 MB** |
| gptme-preloaded via forkserver (warm, single-threaded parent) | **6 ms** | 45.6 MB | **17.5 MB** |
| `KernelBackend` first cell (existing) | 741 ms | n/a | n/a |

Spawn/exec is the portable baseline (the only start method on Windows; the default on
macOS). Forking from the multithreaded server is not safe and is not proposed; a
*separate single-threaded* forkserver parent roughly halves PSS and removes the
import latency, but is POSIX-only and adds a process to supervise. **Warm pools are not
justified by these numbers** at modest concurrency: 216 ms once per conversation start,
hidden behind LLM latency, is cheap. Revisit only if the deployment target is many
hundreds of concurrently-*active* conversations, where 42 MB × N PSS dominates; that is
the capacity figure I could not invent and need from the maintainers (§9).

### 3.2 Warm turn and IPC overhead

JSON-lines over the worker's stdio:

| Operation | Result |
|---|---|
| Warm no-op turn round trip | **0.016 ms** p50, 0.11 ms p99 (300 turns) |
| 1000 streamed 80-byte chunks in one turn | 2.2 ms total |
| 8 MiB result sent inline | 29 ms |
| Confirmation round trip (worker blocks until reply) | works |
| Idle CPU of a worker (3 s window) | 0.0 % |

IPC overhead is negligible next to an LLM call. The 8 MiB case is why large outputs
stay file-backed (#4663): inline is fine, but it blocks the pipe for the duration.

### 3.3 Concurrency scaling

CPU-bound pure-Python turn (~105 ms alone), N concurrent conversations:

| N | threads in one process | N workers |
|---|---|---|
| 1 | 115 ms | 123 ms |
| 2 | 233 ms | 126 ms |
| 4 | 438 ms | 131 ms |
| 8 | 876 ms | 154 ms |

Threads serialize on the GIL (wall ≈ N×); workers were flat **in that run**. This is
relevant only for conversations doing in-process CPU work (Python cells, parsing,
local tools); LLM streaming is I/O-bound and already scales as threads.

**Review-fix rerun:** the full documented `all` command completed after repairing
an invalid start/join loop in the embedded thread probe. It observed 197/4000 wrong
thread reads and zero wrong worker probes, ~260 ms spawn / 41.9 MB PSS, and 0.016 ms
warm-turn IPC. However, 8 workers took 1495 ms versus 1242 ms for threads, so the
initial scaling speedup was **not reproduced**. The cause was not measured; these
uncontrolled single-run timings do not establish deployment throughput or justify
adoption. Lifecycle checks passed, including a live orphan after worker-only kill
and a dead orphan after group kill.

### 3.4 The existing kernel backend as the interim default

`KernelBackend` (`gptme/tools/python_kernel.py`) already gives IPython cells a separate
process with the session workspace as cwd, and a pickle RPC bridge for registered tools.

| Probe | Result |
|---|---|
| Warm cell | 3.3 ms p50, 6.8 ms p99 (vs. in-process ≈ µs) |
| 200 small bridge calls inside one cell | 21.5 ms (~0.1 ms each) |
| 4 MiB bridge round trip | 15.7 ms |
| Live-object sharing: kernel mutates an object obtained from a bridge function | **host copy unchanged** — pickle gives copy semantics, identity is not preserved |
| Kernel cwd | the session workspace (already per-conversation) |

The bridge workloads are fast enough. What disqualifies the kernel as the *default
answer to #5060*:

1. It isolates **only Python cells**. Server-side tools and third-party plugins still run
   in the shared host and still need the contextvar path (`resolve_in_workspace`). The
   issue's own caveat holds: "kernel-only isolation cannot make server-side plugins
   magically cwd-safe." Measured: the kernel does nothing for the shell/read/save/patch
   probes.
2. Live Python objects cannot be shared with host tools (copy semantics above); code
   relying on that breaks. An explicit in-process fallback must remain, as it does now.
3. Requires the `kernel` extra; first cell costs ~741 ms; Jupyter warns it runs over
   **unencrypted localhost TCP** by default (`[IPKernelApp] WARNING | Kernel is running
   over TCP without encryption`). Process separation is not a security sandbox, but this
   widens the local attack surface for code and outputs on multi-user hosts.

Keep it as the per-conversation Python backend that auto-selects today; do not flip the
default for this reason.

## 4. IPC contract (prototyped)

One JSON object per line on a reserved worker stdout pipe; ordinary Python, native,
preload and subprocess output is redirected to stderr, not mixed with protocol frames.
Malformed frames are surfaced as host errors rather than silently killing the reader.
HTTP/SSE stays in the owning frontend server. Implemented in
`scripts/bench_conversation_workers.py` and exercised by
`tests/test_bench_conversation_workers.py`.

```text
host → worker   turn{id,code|prompt}   confirm_reply{id,ok}   quit
worker → host   ready{pid,cwd}   stream{id,chunk}   confirm{id,q}
                done{id,result|error}   cancelled{id}   (host-synthesized) exit{returncode}
cancel          a SIGINT, not a message   (see below)
```

Design points, each exercised by the prototype:

* **Cancellation is a signal.** `SIGINT` interrupted both a pure-Python `while True`
  loop and a blocking `time.sleep(60)` with the `cancelled` reply arriving within ~0.3 ms of the signal, and
  the worker kept serving afterwards (live state intact). A message-based cancel cannot
  interrupt a worker stuck in a C call. Escalation: SIGINT → grace → `SIGTERM` →
  `SIGKILL` to the **process group**. Windows needs `CTRL_BREAK_EVENT` on a new process
  group; not tested.
* **Exit is detected by waiting on the process, never by pipe EOF.** Found the hard way:
  a grandchild that inherited the worker's stdout (an orphaned shell job) keeps the pipe
  open after the worker is `SIGKILL`ed, so a blocking `readline()` hangs forever.
  Likewise `select()` on the fd misses lines already buffered by the text wrapper; use
  a reader thread feeding a queue.
* **Orphans.** After `kill -9` of the worker, a `sleep 300` grandchild was **still
  alive**; a process-group `SIGKILL` killed it. Only its new parent can reap it; a
  remaining `/proc` entry in zombie state does not mean the child survived.
  Workers must be started in their own
  session/group (`start_new_session=True`), and the host kills the group, not the PID.
  This subsumes shell/kernel orphan cleanup for the worker case.
* **Single writer.** The worker takes an `flock` lease for its whole lifetime. A second
  worker for the same conversation was refused (`lease_denied`); after a `SIGKILL`ed
  worker, the kernel released the lease and a restart acquired it immediately — no
  duplicate-writer window and no stale-lock cleanup. Production would reuse
  `LogManager`'s lifetime lock (`.lock`), which already has a Windows lock fallback
  (`_platform_lock`).
* **Crash visibility.** The host sees `exit{returncode=-9}`; a *tool error* is a
  `done{error}` and does not kill the worker. Restart is from the durable log.
* **Durable vs live state.** Live Python state (`state` dict in the prototype) persists
  across turns and is **lost on eviction or crash**; durable history is the log. Eviction
  policy (idle TTL) therefore must be user-visible, the same as kernel restart today.
* **Large outputs** go through the file-backed output path (#4663), referenced by path in
  `stream`/`done`, not inlined.

## 5. Wake and notification boundary (audited)

Current mechanisms — they share primitives but **not one transport**:

| Event | Producer | Durable? | Reaches idle server? |
|---|---|---|---|
| Peer mail | other process → `prompt_queue.py` (`prompt-queue.jsonl`, flock) | yes | **yes, by polling**: `_run_peer_wake_check` every `_PEER_WAKE_INTERVAL = 2.0` s → `request_peer_wake` |
| Watch event | in-process queue | no (re-queued on failed wake) | yes, `request_watch_wake`; retried at step end by `retry_deferred_watch_wakes` |
| Subagent completion | in-process queue + `notification_ready.signal` | no | yes, via `request_watch_wake` (`subagent/hooks.py:348`) |
| Shell background job completion | `shell_background._completion_queue` + condition | no | **no idle wake found** (below) |
| Progress | in-process queue, drained into the next wake | no | rides along; never wakes by itself |

**Audit finding — shell-background completion has no idle wake.** `_notify_completion`
(`tools/shell_background.py:1585`) enqueues to `_completion_queue` and notifies a
condition; it neither calls `notification_ready.signal` nor `request_watch_wake`, and
`shell_background.py` references no `SessionManager`. `retry_deferred_watch_wakes`
drains only watch events and subagent completions. So an idle server conversation is not
woken by a finished `run_in_background` job; the result is picked up by the
LOOP_CONTINUE hook only when some other event starts a step (and
`background_job_wait_hook` subscribes to `notification_ready` for *child* wakeups, not
for job exit). This is a parity gap versus subagent completion, independent of worker
processes, and is the cheapest concrete consolidation: make job completion call the
same `request_watch_wake` path (event-specific message, same busy/autowake policy).
**Verified** (follow-up pass): `tests/test_wake_admission_matrix.py::test_shell_background_completion_has_no_idle_wake`
starts a real `printf` job through `start_background_job`; on exit the job is queued
(`_completion_notified`, present in `_completion_queue`) but `request_watch_wake` and
`notification_ready.signal` are never called. This is a unit-level reproduction against
the real module, not a live-server run.

Shared already: `_conversation_busy` and `_dispatch_reserved_step` (admission +
dispatch) are used by both the watch and peer paths, and `use_acp` conversations
explicitly defer both (so a wake never answers a conversation whose input cursor a
different runtime owns). `session.interrupted` suppresses peer wakes (a user-stopped
conversation is not restarted). **`request_watch_wake` does not check it and clears it**
(`session.interrupted = False` at reservation), so a subagent completion or watch event
restarts a conversation the user stopped. Pinned by
`test_interrupted_watch_wake_restarts_conversation_today` next to
`test_interrupted_peer_wake_leaves_conversation_stopped`. Whether that is intended (a
finished child is something the user asked for) is a maintainer decision; either way the
unified admission step of §5.1 must make it one explicit rule per `kind` rather than two
code paths that drifted.

The idle/busy/ACP/interrupted matrix for peer and watch wakes is exercised against the
real `SessionManager` (dispatcher and config mocked) in
`tests/test_wake_admission_matrix.py`; run it with
`pytest tests/test_wake_admission_matrix.py`.

### 5.1 Proposed conversation-addressed inbox (design only)

Split **enqueue** from **doorbell** from **admission**:

1. **Enqueue (durable, any process).** Append a record to the conversation's inbox
   (generalize `prompt-queue.jsonl`) with `event_id` (ULID/UUID), `kind`
   (`peer|watch|child_done|job_done|progress`), `branch`, `sender`, `created_at`, body.
   Event-specific semantics stay in `kind`: progress is non-waking, completion and
   peer mail may wake. Enqueue is idempotent by `event_id`.
2. **Doorbell (optional, lossy).** A payload-free signal — in-process
   `notification_ready.signal(logdir)` today; across processes a unix-domain socket /
   named pipe or the worker's `{"t":"event_ready"}` line. Losing it is harmless: the
   2 s poll (kept as the fallback) finds the record. No callbacks are shared across
   processes.
3. **Admission (single owner: the host that owns the lease).** Reserve a step iff the
   conversation is idle (`_conversation_busy` equivalent), not user-interrupted, not
   ACP-owned, `watch_autowake` not disabled; otherwise leave the record queued for
   STEP_PRE drain or the next idle poll. Backpressure: bounded inbox length/bytes per
   conversation with explicit drop-oldest-`progress` / reject-with-error for peer mail;
   reply-loop prevention by a `hops`/`in_reply_to` chain limit on peer messages.
4. **Delivery and ack.** The step that persists the event into the log writes
   `event_id` into the message metadata *in the same append*, then marks the inbox
   record acked. Crash between append and ack → on restart the record is redelivered but
   deduplicated against log metadata. This is **at-least-once with idempotent
   persistence, not exactly-once execution**; the model may still observe a retried
   *wake* once. Draft preservation: a wake never consumes or edits an in-progress user
   draft; it only appends system messages before the next step.
5. **Branch routing.** `branch` is carried on the record; admission remains
   conversation-wide (§2), so two branches' events are serialized, not run in parallel.

The existing code already does steps 1, 3 (partially) and 4 (persist-then-drain with a
documented "True even if dispatch failed" rule to avoid duplicate replay). What is
missing is the `event_id` ack/dedup, the unified `kind`, and job-completion
participation.

### 5.2 Cross-harness adapters (design only; not implemented, not verified live)

Advertise capabilities explicitly rather than a boolean `addressable`:

`observed` (we saw a PID/cwd) < `addressable` (a stable handle owned by a managed
runtime) < `enqueueable` (durable inbox the owner reads) < `boundary-deliverable`
(owner drains at a safe point) < `wakeable` (owner can start a turn while idle).

A discovered PID is at most `observed`. For Claude Code, the supported owner-side
surface is its hook system (e.g. `UserPromptSubmit` / `PreToolUse` `additionalContext`):
a hook that drains the durable inbox gives `boundary-deliverable` for **any** session,
managed or not, with no transcript edits and no keystroke injection. `wakeable` requires
the harness to be launched/owned by a runtime with a programmatic prompt API (headless
`claude -p --resume`, an SDK/app-server session, or a managed ACP session) — not an
arbitrary interactive terminal. I did not verify Codex's current app-server/resume
surface in this pass; do not promise injection there until someone does. Peer discovery
should keep reusing `hooks.workspace_agents.scan_agents` (extract the scanner/runtime
parsers/`AgentInfo` into a dependency-light module with a cheap discovery path and
optional git/staleness enrichment, as the issue comment proposes); managed workers
should publish explicit conversation endpoints, with process scanning as the fallback
for unmanaged runtimes.

## 6. What plumbing could be deleted

Honest answer: **nothing yet.** Deletion is only valid after worker mode is the sole
server execution path; during any hybrid period both paths run and both need the
plumbing.

| Plumbing | Fate if workers become the only server path |
|---|---|
| `_inprocess_cwd_lock`, `_inprocess_cwd_tls`, `suspend_inprocess_session_cwd` (`tools/python.py`) | deletable for the server (the in-process cell *is* the worker's main thread; the cwd is native). Retained for CLI/TUI only if they still use a per-thread pin — they do not; CLI uses process cwd |
| Per-context `set_workspace_cwd` / `get_workspace_cwd` for **plugin filesystem behaviour** | deletable inside the worker: `current_workspace()` collapses to `Path.cwd()` |
| `current_workspace()` / `resolve_in_workspace()` call sites | **retain** as the API (tools still call it, and it must keep working for CLI and host-resident code); they become trivial rather than load-bearing |
| Kernel RPC `cwd` pass-through (`python_kernel.py:284`) | retain while the kernel backend exists |
| Host-side: lease, process-group supervision, IPC framing, doorbell | **new** plumbing; the net deletion is smaller than the net addition |

Third-party plugins stop needing to virtualize the filesystem, which is the real
payoff; the in-tree deletion is modest.

## 7. Recommendation

**Stage it:**

1. **Now:** land #5059 (decoupling + hostile-host-cwd regression coverage). Fix the
   shell-background idle-wake gap (§5) independently; it needs no worker.
2. **Prototype in-tree behind an opt-in flag** (e.g. `GPTME_SERVER_WORKERS=1`): lazy
   `spawn` worker per conversation, JSON-lines contract of §4, `LogManager` lock as the
   lease, process-group supervision, host-side admission unchanged. Start with the
   Python/shell tools and one non-trivial plugin.
3. **Measure with the real worker** (full `init_tools()`, real streamed turn, macOS and
   Windows) against capacity targets before deciding on a default or a warm pool. Only
   then consider a forkserver zygote on POSIX.
4. **Do not** make the kernel backend the default for #5060 (§3.4).

Reject conditions to watch for: real-worker PSS well above ~60 MB at the intended
active-conversation count, or tool state that depends on live host objects across the
process boundary (MCP clients, browser threads, `watch` registrations all live in the
host today and would have to be re-homed or proxied — see gaps).

## 8. Known gaps in this pass

* Prototype exec's code strings; it does **not** run real gptme tools, an LLM turn, or a
  third-party plugin end to end. The `open`/`Path.exists`/`subprocess`/native-`getcwd`
  isolation result is real, but "plugin unmodified" was demonstrated with a stand-in.
* Peer/watch wake during busy/idle/ACP/interrupted states and the shell-job no-wake gap
  are now demonstrated against the real admission code (§5,
  `tests/test_wake_admission_matrix.py`), with the step dispatcher mocked. Still not
  demonstrated: those events crossing a *process* boundary into a worker, and a live
  server run. §5.1 remains a design.
* Host-resident state that does not obviously survive a process boundary and needs an
  owner decision: browser tool thread, MCP client connections, `watch` registrations,
  subagent execution (not audited in this pass whether children run as threads of the parent; that decides whether they stay inside the parent's worker).
* macOS and Windows startup numbers, `spawn` semantics for the preload, and the
  Windows cancel path are unmeasured.
* No deployment capacity number was given, so no pass/fail thresholds are asserted.

## 9. Questions for maintainers

1. Target number of concurrently *active* (not merely stored) conversations per server?
   This sets the PSS budget and whether a warm pool is ever warranted.
2. Is a branch with a different runtime/workspace a supported or planned feature?
3. Should subagents become workers of their own, or remain threads inside the parent's
   worker?

## 10. Reproduce

```bash
uv sync --all-packages && uv pip install jupyter_client ipykernel   # kernel probe only
python scripts/bench_conversation_workers.py all --preload gptme.chat,gptme.server.session_step
# individual: startup | ipc | scaling | isolation | lifecycle | kernel
python scripts/bench_conversation_workers.py scaling --max-workers 8 \
    --preload gptme.chat,gptme.server.session_step
```

Each subcommand prints one JSON document including interpreter, platform and argv.
Numbers above are a single run on one machine; p50/p99 are across the in-run samples.
