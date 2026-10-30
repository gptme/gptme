---
audience: developer
---

# Transport recovery boundaries

Developer audit and implementation scope for [#4657](https://github.com/gptme/gptme/issues/4657).
The CLI/TUI recovery boundary and its limits are described below. Verification
uses offline provider faults and loopback SSE fixtures, not paid inference.

## Entrypoints

Paths below are relative to the repository root.

| Entrypoint | Before shared recovery | Recovery behavior / boundary |
| --- | --- | --- |
| CLI interactive: `chat._chat` → `chat.step` → `llm.reply` | Adapter retries only before output; tagged failures return control to the user and clear queued prompts. | `reply()` retries an uncommitted generation below tools. Exhaustion parks remaining prompts on disk and returns control; they are not auto-drained under a fresh budget. |
| CLI headless: same generation path | Provider failure propagates to `cli.main`, records terminal error and exits nonzero. | Same bounded generation recovery; remaining prompts are parked on disk, then this process still exits nonzero. |
| TUI: `GptmeApp._generation_body` → `chat.step` | Failure ends the worker; app stays open and may dispatch the next queued turn. | Shared `reply()` recovery and `_on_retry` preview reset stay inside the worker; committed messages and queued steering are not reset. |
| Server: `server/session_step.py::step` | Custom `_stream` / `_chat_complete` loop, overflow recovery, epoch-guarded commit; failure emits error and releases generation. | **No automatic transport replay**: bypasses `reply()`. Needs server-specific preview reset, cancellation and `step_seq` ownership integration. |
| ACP-backed server/session paths | ACP streams `chat.step` previews via `on_token` in an executor thread. | **Automatic replay explicitly disabled** with `recovery_disabled()` around step creation and consumption in that worker: ACP has no preview-reset protocol. Shared request accounting and adapter-retry suppression remain active; failures retain ACP's error/cancelled response. |
| Thread/subprocess subagents | Thread generation uses core chat; subprocess monitor reports nonzero exit as failure, without respawn. | Generation through `reply()` inherits recovery. Process-level restart remains explicit `subagent_continue`, not automatic recovery. |
| Scheduled agent: `agent.workspace.generate_run_script`, `agent.service` | One noninteractive invocation per scheduled run. | A later timer run is new scheduled work, not guaranteed resumption of the failed turn. |

## Shared generation contract

- `gptme/llm/recovery.py` owns recovery around provider work in `llm.reply()`.
  `GENERATION_PRE` hooks run before replay; tools and post-generation hooks are
  outside it. Never wrap `chat.step()` or tool execution in a blanket retry.
- One context-local `RecoveryBudget` is reused across nested calls. Adapter-local
  retries are suppressed inside the scope; physical sends, including capability
  and degeneration fallbacks, debit `reserve_recovery_request()` before sending.
  SDK retries remain disabled (`retry_policy.SDK_MAX_RETRIES = 0`). HTTP
  redirects are refused inside recovery so 307/308 cannot silently resend a
  POST outside the allowance; direct calls outside recovery retain their policy.
- `GPTME_LLM_MAX_RETRIES` supplies the allowance (default 11, including initial
  attempt). The **600-second monotonic deadline bounds admission and backoff,
  not an already-blocking provider request**. It is not a hard in-flight timeout;
  adapter network timeouts remain relevant.
- `chat._reply_with_overflow_recovery()` establishes the scope outside
  `tools/autocompact/recovery.py::recover_reply`: smaller context views must not
  reset the send allowance or deadline. Overflow recovery remains a separate
  context-length policy, not transient transport classification.
- Replay requires provider-origin tagging plus an allowlisted transport type,
  HTTP status (408/429/5xx), or transient Responses error code. Authentication,
  permission, malformed requests, resource limits and tool/hook failures must
  not become blanket-retry cases. Respect `Retry-After` and strict request limits.
- Interactive CLI exhaustion parks remaining in-memory prompts on the durable
  queue and holds drain/consume until the user submits a normal message.
  Slash commands and Ctrl+C do not retry parked work. Headless exhaustion
  still exits nonzero without auto-resume.
- Failed output is an uncommitted preview, not persisted assistant content.
  Callback consumers bind `recovery_notifications()` and reset attempt-local
  display/buffers before replay. Plain terminal output cannot be retracted;
  retry notices identify discarded output rather than presenting concatenated
  attempts as one successful response.
- Once a `GENERATION_CHUNK` hook has been invoked, replay is disabled: emitted
  TTS or other external effects cannot be rolled back. Cancellation must escape
  replay/backoff, without committing a partial response as successful.

## Completion and side-effect safety

`llm._reply_stream()` can break on a complete runnable tool and drain remaining
provider events for metadata before returning. A drain failure must not execute
the failed attempt's tool. Server custom streaming has a separate early-break
path; it does not inherit this safety contract automatically.

`ToolUse._iter_from_tool` requires a complete JSON boundary before parsing;
Responses native calls are withheld until reconciliation. Neither syntactic
completeness nor natural iterator exhaustion proves provider completion.
Chat Completions requires a non-null `finish_reason`, Anthropic requires
`message_stop`, and Responses requires a terminal completed/done/incomplete
event. Subscription `[DONE]` remains an accepted terminal sentinel. Natural EOF
without a terminal raises a typed transport failure before native reconciliation;
explicit provider failure codes and incomplete-response semantics remain distinct.
Intentional consumer closure is not interpreted as premature EOF.

Committed earlier tool results must survive retries. CLI/TUI `chat.step()` still
buffers the current assistant/tool results until execution finishes: this work
is not a crash-safe, exactly-once tool journal or a guarantee against replay by
an external process after a crash.

## External runner audit (not fixes in this change)

These observations concern separate local checkouts, not core guarantees:

- `gptme-contrib/packages/gptme-runloops/.../utils/execution.py::execute_gptme`
  launches once, copies its trajectory, then removes private staging logs.
  Its `shell=True` `gptme … | tee …` pipeline lacks pipefail: tee's success can
  mask gptme's nonzero exit. It does not automatically resume the conversation.
- Runloops `utils/run_once.py` supports explicit `resume=<session_id>` by reusing
  a named conversation; that capability is not automatic failure recovery.
- Bob's `scripts/runs/autonomous/autonomous-run.sh` has a bounded, named-session
  429 resume path (two attempts, cooldown and lane-lock reacquisition). Its entry
  guard requires exit **1** plus a 429 stderr match, while current core classifies
  rate limits as exit **75**. Chunked transport failures do not match this path.
  Resume timeout remains nonzero (124). A later unrelated run is not a substitute.

The regression suites `test_llm_generation_recovery`,
`test_subscription_terminal_recovery`, `test_sdk_stream_terminal`,
`test_recovery_nested_budget`, `test_chat_transient_recovery` and
`test_tui_recovery` cover terminal-less EOF, partial preview reset, truncated
JSON/no execution, retained earlier tool results, queue retention, cancellation,
bounded physical sends, permanent errors, accurate exhaustion and interactive
CLI parking of remaining queued intent.
