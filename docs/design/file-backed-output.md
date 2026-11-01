---
audience: developer
---

# File-backed tool and process output

**Status:** proposed contract; no production capture changes in this PR.

**Request:** [gptme/gptme#4663](https://github.com/gptme/gptme/issues/4663).

**Source inventory:** `15f20e44c96acdd035da81213b9e9f5bb54ac98d` (2026-10-30).

## Decision

Extend `util/output_storage.py` and `tools/shell_background.py`, keeping
conversation-owned `tool-outputs/` as the storage namespace. Keep small results
inline; return bounded useful previews and explicit retrieval references for
large results. Capture completeness, process completion, and preview truncation
are independent facts. A saved tail is not a complete log.

Do **not** introduce another scratchpad tool, artifact database, index/retention
daemon, semantic-summary service, or implicit prompt attachment. Do not change
the attachment/restart batch to implement this design. A path in a receipt is
untrusted output metadata, not permission to read or attach its contents.

This complements [transcript persistence](transcript-persistence.md): transcripts
preserve received messages; process capture must preserve output *before* it is
omitted from those messages. [PTC](ptc-tool-interface.md) defines invocation, not
capture. TUI folding, call/result association, context compaction and generation
recovery are separate layers, not alternative output stores.

## Current-state inventory

References below are source paths and function names at the inventory revision.
A file may preserve a selected/decoded/redacted representation without preserving
original process bytes. No generic guarantee applies across these rows.

| Entry point | Capture and presentation today | Retention/retrieval limitation |
| --- | --- | --- |
| `util/output_storage.py:save_large_output` | Writes an already-materialized string; second-resolution timestamp + eight-character content hash | Ordinary mkdir/write; no explicit private modes, exclusive creation, atomic publication, containment check or inherent redaction. Same content/type/second reuses a path. |
| `tools/shell.py:ShellSession.run`, `_format_shell_output`, `_shorten_stdout` | Foreground collects decoded strings, normally under a 32 MiB combined raw-read cap; ANSI removal and configured credential redaction precede formatted spills. Line/token shortening and query-aware pruning save their input first when logdir exists | Full *supplied text*, not original bytes: UTF-8 replacement, shell framing/blank trimming and sanitation already occurred. Strings/tokenization still use RAM; spill is not streaming. Missing logdir means no durable spill. |
| `tools/shell.py:execute_shell_impl` promotion | Existing foreground worker continues; job callback exposes active output. `complete_background_job` writes final text before storing a bounded tail | Existing cap/termination path remains relevant; complete relative to returned text, not an unlimited process lifetime. No launch-time spool. |
| `tools/shell_background.py:BackgroundJob._read_output`, `get_output`, `format_job_output` | Direct jobs incrementally decode into roughly 1 Mi-character tails per stream (constant counts characters, not bytes); display tails 8k stdout / 2k stderr. `output N --new` advances character cursors | Prefixes can be evicted before first disk snapshot. Sanitized snapshots on display, not append-only spooling. Promoted final writes are protected against later snapshot replacement; direct snapshots are mutable. |
| Background termination | Reader drains for about one second after direct-child exit, flushes decoder, then notifies; process-group cleanup handles descendants | Reader-finished does not prove pipe EOF or unlimited descendant capture. Registry/cursors are in memory; jobs do not survive restart. |
| `tools/python.py:TeeIO`, `_cap_output`; `python_kernel.py:KernelBackend.execute` | In-process/kernel stream capture keeps bounded heads (10 MiB per stream); ordinary result/error text materializes before capping. Returned Message paths differ | No direct output_storage spill; omitted bytes are unrecoverable. Live tee can show more than transcript. Sandbox Docker communicate / Wasmtime file read can materialize output before clipping. |
| `tools/gh.py:execute_gh`, `util/gh.py` | Subprocess capture buffers before native parsing. Native run view extracts/clips failure sections; PR thread bodies also clipped; passthrough has no general spill/cap | No archival guarantee. Re-fetch requires credentials/network and may return changed state; successful passthrough stderr is not yielded. |
| `tools/mcp_adapter.py:create_mcp_execute_function`, resource/prompt helpers | Loaded tool conversion returns first TextContent, not all content items; resources join text, other content has placeholders | No generic spill/cap. Transport limits are external. First text can enter debug logging. Advisory MCP roots are not sandboxing. |
| `tools/browser.py`, `_browser_playwright.py`, `_browser_lynx.py` | HTML/Markdown/ARIA and subprocess results materialize; console logs accumulate; PDF download materializes, default first 10 pages is not a byte bound | No direct text spill. Python-call path meets Python caps; native function conversion has no equivalent generic cap. Screenshots/auth-state files are separate resources. |
| `tools/read.py:_read_one`, `_read_text_bounded`, `_read_line_range` | Whole-file text limited to 1 MiB; ranges have rendered-byte bounds/continuation. Conditional relevance pruning saves selected pre-prune text | Saved selection is not necessarily the whole input. Original file may change later. `GPTME_READ_ROOT` confines resolved paths; default absolute-path access is broader. |
| `tools/tmux.py:_capture_pane`, `_truncate_output` | Captures scrollback into RAM, then 50 head / 150 tail lines; native inspect/wait can spill to user-cache-root tmux-output | No byte cap; new/send paths do not supply spill logdir. Pane history may already have lost older output. Cache spill is not conversation-owned. |
| `tools/autocompact/engine.py`, `create_tool_result_summary` | Saves received message text when logdir exists, then replaces with pointer; master-log references are another mechanism | Cannot recover upstream omissions or prevent initial allocations. Without logdir it can remove text without a saved file. |
| CLI/TUI and shared `ToolUse.execute` | Tool/call results can be collected before persistence. CLI transcript stores canonical message; TUI folding is display-only; TUI stdout/stderr also append to tui.log | No universal bounded capture or sanitation layer. A tool crash before step persistence can precede recording. TUI log is not output_storage. |
| Server | Tool results collected and emitted as tool_output/message events; conversation transcript remains canonical | Event-count bound is not a byte bound. Existing authenticated `GET /api/v2/conversations/<id>/files/<filepath>` can serve logdir-relative tool-outputs, subject to resolved containment; workspace fallback also exists. Endpoint serves bytes, not newly redacted text. |
| ACP / gptme MCP server | ACP forwards message text, not file-transfer handles; MCP server joins tool output | No verified artifact transfer bridge or universal cap. ACP adapter does not enforce Message.hide as an access boundary. |
| `tools/recall.py:_lookup_master_result` | Stable current-master-log message positions, paged character retrieval | Recovers what was recorded, not omitted process bytes or missing spill files. A recall result ID is not a filesystem resource ID. |

Adjacent process paths are not covered by shell's contract: `tools/watch.py`
`_poll_until` uses communicate before emitting a 500-character condition preview;
`_poll_run` keeps a 1,000-character tail; `_poll_stream` bounds event count but
accumulates partial-line text separately. `tools/convert.py` also uses subprocess
capture before reporting diagnostics. Neither offers a general output_storage
spool. Inventory/adoption is separate from the direct-job pilot; do not duplicate
the bounded watch-probe work in gptme/gptme#4639.

Credential guardrails before execution are useful but are not universal output
redaction. The generic writer is unsanitized. Shell job sanitation has explicit
handling for partial assignments and evicted line prefixes; preserve that policy
when moving persistence earlier. Do not advertise a general secret-scanning guarantee.

## Evidence: existing storage versus smaller presentation

Run from the repository with its Python environment:

```sh
.venv/bin/python scripts/measure_output_capture.py
```

The script uses synthetic fixtures, no provider or network calls, disables
network-capable tokenizer loading and sets explicit default shell budgets
(2k/8k stdout, 2k/2k stderr). It compares actual foreground formatting/storage
with a **presentation-only candidate** using 8k/2k character head/tail budgets.
The candidate is not a streaming implementation or validated user-experience win.
All token figures below are character/4 approximations, not billed tokens.

One local sample (temporary path lengths cause small byte-count differences):

| Fixture | Input bytes | Current inline bytes (~tokens) | Candidate inline bytes | Saved text bytes |
| --- | ---: | ---: | ---: | ---: |
| Compact success | 19 | 77 (~20) | 163 | 0 |
| Pytest-style 20k result lines | 900,022 | 40,198 (~10,050) | 8,150 | 900,022 |
| API JSON, 10k objects | 408,891 | 40,194 (~10,049) | 8,148 | 408,891 |
| Compiler failure, exit 2 | 358,928 | 56,392 (~14,098) | 10,167 | 358,928 |
| CSV, 30k rows | 318,905 | 40,208 (~10,052) | 8,155 | 318,905 |
| Huge single line | 2,097,176 | 40,212 (~10,053) | 8,157 | 2,097,176 |

Every nonempty stream was exact in its saved text or present inline; failure
code and final error remained visible in both presentations. No redaction/binary
fidelity conclusion follows from these ASCII fixtures. Smaller previews save
about 80% of existing inline bytes on these large cases, but a universal receipt
*increases* compact-success overhead: keep the existing cheap small-result path.

Formatting/storage took approximately 0.3–60 ms with warm imports and
tracemalloc enabled; measured formatter peak Python allocations were 3 KiB–3.8 MB,
**excluding prebuilt input strings**, not whole-process RSS. A local reread scan
found the compiler error in 0.5 ms; each spilled case used one simulated selective
local retrieval operation (one file open per saved stream), zero for compact
success; no external tool calls were measured. Completeness validation reads
are outside retrieval timing; timed retrieval starts before reopening the files.
Fixture checks also recovered a
specific API object, CSV row, pytest summary and huge-line boundary markers.
Reread/selection took 0.5–3.6 ms and currently materializes the text in the
evidence script. These are single-sample
mechanism observations, not latency benchmarks, model-success scores or measured
end-to-end time-to-useful-result. External/tool-call latency remains unmeasured.

A real subprocess emitted 2,340,024 ASCII characters through the direct background
reader and exited 2. It retained about 1.05M characters, evicting about 1.29M
(chunk-dependent); the saved snapshot lacked BEGIN-MARKER and explicitly reported
loss. There was no path before the first snapshot. Reader capture took about
32 ms / 1.07 MB peak traced Python allocations in this sample. In contrast,
feeding that complete text to the promoted final writer retained BEGIN/END and
a later live snapshot could not replace it. This proves the paths differ; it
does not prove promoted raw-byte fidelity or crash durability.

## Proposed contract

### Representation, identity and inline receipt

Use existing conversation ownership and tool-call/job identity; add a unique
capture identity only where no call identity exists. A content hash is an
integrity check, not identity or authorization. Separate stdout/stderr resources
preserve stream order individually; do not invent a total cross-stream order.

Each large-result receipt retains: call/job/capture ID, bounded command label,
process state, actual exit code or signal, cancellation/timeout reason, capture
state/reason, representation, per-stream observed/retained byte counts, preview
omissions, and conversation-relative reference plus resolved local path when
usable. Line counts are optional for text, with trailing partial-line semantics
specified. Unknown counts are unknown, never zero or an inferred total.

Use independent states:

- **Process:** running, exited, cancelled, timeout (observed code separate).
- **Capture:** pending, complete, partial, failed, disabled, unavailable.
- **Representation:** redacted UTF-8 text, replacement-decoded text, or explicit
  binary bytes. A complete redacted representation is not byte-identical raw output.
- **Preview:** complete or head/tail omitted, with its own returned-byte budget.

Keep nonzero exit/signal and capture errors outside clipped body text. Successful
small results need no extra file by default; jobs expose a receipt/live resource
from launch. Initial preview candidate: shared 8k/2k character ceilings with an
additional combined UTF-8 byte ceiling (e.g. 16 KiB); head plus larger tail,
prioritizing stderr on failure. The extra byte ceiling is not tested by the ASCII
candidate. Budget selection stays configurable; do not silently replace existing
user settings. Semantic summaries are optional derived views, never evidence.

### Capture before eviction

For process paths, spool **sanitized representation from the first read**, before
bounded preview buffers evict anything. Reuse output_storage placement and job
lifecycle; do not run a second pipe reader. Incremental decoder/redactor state
must span chunk boundaries. Bound the pending line/token buffer: if sanitation
cannot decide within a bounded window (huge line/partial assignment), omit it
with an explicit redaction/representation-loss marker. Never spill uncertain raw
bytes merely to claim losslessness. Original raw bytes need an explicit policy
and are not a default second copy of secrets.

Proposed first limits: retain the foreground combined 32 MiB output ceiling;
apply a configurable 32 MiB combined retained-stream ceiling to direct jobs,
plus a 128 MiB conversation allowance for new spools. These are conservative
starting proposals, not measured optimal defaults. On quota/storage failure,
stop persistence, retain bounded preview, and mark partial/failed with the known
retained boundary; continue draining/discarding so the child cannot deadlock.
Writer backpressure must also be bounded: a bounded writer queue may stop
accepting persistence on saturation, mark capture partial, and let the sole
pipe reader keep draining. No unbounded queue or synchronous slow-file write
in that reader. The pilot does not promise recovery from an indefinitely
blocked kernel filesystem write; isolate that writer and report the limitation.
Do not silently kill a build because its log disk filled. Existing explicit
process output/memory-kill policy remains separately reported. Concurrent jobs
must reserve against the shared allowance, with restart reconciliation from
conversation-local files; no database is necessary.

No unbounded readline, StringIO, frame join or tokenizer pass on the whole spool.
Binary/invalid UTF-8 gets bounded safe display and explicit encoding/transform
metadata; raw binary retention is opt-in. Preserve original exit code even if
preview/storage/summary fails. If a descendant holds pipes beyond the drain
window, finalize as partial with drain-timeout, not EOF-complete.

### Live reads and immutable completion

Use separate `.live` stream files and immutable final names/receipt. A live
resource advertises running state and committed readable length; its contents
can grow and must not be cited as fixed evidence. After readers finish, flush
and close, atomically publish final metadata *last*. Complete capture requires
actual EOF and successful persistence; cancellation can have complete capture
through EOF but is not successful execution. Crash leftovers remain partial
and discoverable, never silently promoted to complete on restart.

Keep byte offsets per capture/stream and explicit retained start/end, distinct
from existing decoded-character cursors. Live cursors denote committed retained
representation bytes, not raw pipe bytes. UTF-8 range rendering aligns/decodes
safely. Committed readable bytes must already satisfy the selected sanitation
policy: a later rewrite cannot retract a credential a reader already saw.
Hold undecidable fragments privately within a bounded window, then omit them;
no security-repair rewrites in the pilot. Non-security replacement invalidates
the cursor generation explicitly rather than duplicating/skipping bytes.
No rotation in the first
slice: cap explicitly. A poll advances only that consumer's cursor; one automatic
completion receipt has an independent delivery marker so reading output does not
consume notification ownership. Promotion must adopt the existing capture at a
known boundary, never copy/replay chunks into a second spool.

### Namespace, permissions and access

Create directories privately (`0700`) and files privately (`0600`, appropriate
Windows ACL equivalent), before writing content. Use exclusive unguessable
creation and safe descriptor-relative traversal/containment where supported;
reject symlink/non-directory parents and unsafe types. Atomically publish whole
text snapshots. Do not chmod or overwrite arbitrary existing targets. Hashes and
private modes alone do not make a symlink race safe. Command labels, paths and
metadata also need sanitation; arbitrary secret argv has no perfect automatic
redactor, so allow omission.

Extend existing storage helpers instead of introducing a second authoritative
manifest. A small per-capture final sidecar is sufficient for receipt recovery;
existing context-savings telemetry remains derived. Do not deduplicate by a
short hash: distinct calls keep distinct identity. Any later content dedup must
preserve access scope, metadata, lifetime and accounting.

Logs follow conversation lifetime, not ephemeral `/tmp` scratch. No automatic
git commit or cloud upload of raw output. Export is explicit: include permitted
resources and rewrite relative references, or mark them unavailable. Deletion
of a conversation may remove its logs under existing policy; a child handoff
must copy/share permitted resources before owner deletion if retention is needed.
Quota exhaustion refuses new retention, **not** deletion of historical evidence.

For local readers, ordinary bounded read/grep/tail/jq is enough. For server
clients, resolve conversation-relative refs through the existing authenticated
file route, verifying authorization/containment and transfer-size limits; do not
assume it supplies range/paging semantics. ACP/sandbox/remote children without
that route receive an explicit unavailable/needs-transfer state, not a fictitious
working host path. Any new transport bridge must reuse session authorization.
A parent/child receipt shares only permitted output, identity, exit/capture state
and locator; never copies full transcripts or auto-attaches incidental paths.
Re-read output stays untrusted data with original producer provenance and the
same injection-screening/access policy; local storage does not confer authority.
Security-sensitive subprocess paths that deliberately suppress output remain
non-spooling unless explicitly opted in under their policy.

## Small implementation sequence and regression gates

This PR delivers the inventory, reproducible evidence script and contract.
Implementation defaults below await maintainer agreement; broad rollout is not
implied by existing tests passing.

1. **Safe existing writer + honest receipt.** Harden output_storage creation,
   containment/private permissions and atomic whole-text publication without
   changing tool schemas; retain backward-compatible path/text returns. Add
   representation/completeness metadata with explicit unknowns. Test same-second
   concurrent calls, symlink parents/destination, unsafe output_type, permissive
   umask, restrictive existing file, ENOSPC/short write and failure before publish.
   Existing writer-safety tests do not cover those cases. Review permission-
   preserving transcript/export writers for reusable primitives, not assumed safety.
2. **Direct background spool pilot.** Thread capture state through the existing
   reader/job registration and completion APIs; expose launch-time ref; spool
   sanitized chunks before tail eviction. Test BEGIN/END beyond 1 Mi-character
   buffer, both streams, >32 MiB quota, huge line, split UTF-8/credential chunks,
   actual EOF versus drain timeout, disk failure, kill/cancel and independent
   consumer cursors. Count raw observed, raw accepted for transformation, raw
   pending and raw discarded/unprocessed separately: observed = accepted +
   pending + discarded. Count committed representation bytes independently;
   they can expand/contract under decoding/redaction. EOF/cancel/quota/write
   failure must settle pending bytes explicitly and report uncommitted transformed
   output without equating retained-file size to accepted raw bytes.
3. **Foreground/promotion convergence.** Reuse that capture, retaining existing
   shell framing/output cap and irreversible execution semantics. Test promotion
   at chunk boundary, finish racing poll/kill, no duplicate completion or chunks,
   exact nonzero exit code, stderr-only failure, no lost buffered decoder tail,
   and immutable final evidence despite later output/wait calls. Smaller previews
   are a separate policy change with fixture before/after evidence.
4. **Entry-point adapters, only when verified.** Python/kernel, gh/MCP/browser
   conversion, tmux/read and autocompact each declare what they captured before
   adopting the receipt. Do not label upstream truncation complete. Test missing
   files, restart recovery, explicit export/child sharing, authorized remote
   retrieval/denial and range caps. ACP and sandbox retrieval remain unavailable
   until a real bounded transfer is tested.

Every pilot checks terminal failure visibility, private-mode/redaction controls,
producer memory/disk limits and no implicit attachment. Add end-to-end selective
retrieval tasks (find failing test/error, jq one API object, read CSV range),
recording useful-answer correctness, extra tool calls, context bytes/tokens and
time-to-useful-result separately from formatter time. Current offline evidence
justifies safe storage and a direct-job pilot; it does **not** justify universal
capture, a default-policy flip, or a claim of improved agent task success.

## Alternatives deliberately rejected

- **Only shrink previews:** easy context savings, does not repair lost job prefixes.
- **Persist everything raw:** captures secrets and increases disk/privacy exposure;
  preservation must be qualified by policy and representation.
- **Redirect every command manually:** useful workaround, but ad hoc ownership and
  pipeline exit-status mistakes remain; `tee`/`tail` do not preserve failure by magic.
- **New shell_capture/read_capture tools:** duplicate existing invocation/storage
  surfaces; no demonstrated need for another tool API.
- **Generic post-tool spill alone:** helpful for presentation, too late for RAM,
  MCP content conversion loss, bounded Python capture or evicted background data.
- **A central database, remote store or cleanup daemon:** unnecessary for the
  observed conversation-local gap and creates new lifecycle/security obligations.
