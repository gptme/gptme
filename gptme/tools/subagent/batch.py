"""Subagent batch execution — parallel task management.

Provides BatchJob for managing groups of subagents and subagent_batch()
for convenient fire-and-gather patterns. Also provides subagent_parallel()
for a simpler synchronous fan-out pattern. subagent_pipeline() provides
staged fan-out with no barrier between stages — item A advances to stage 2
while item B is still in stage 1.
"""

import copy
import json
import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures import wait as futures_wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, cast

from .api import subagent, subagent_cancel, subagent_wait
from .types import ReturnType, SubagentBudget

logger = logging.getLogger(__name__)


def _strip_log_suffix(text: str) -> str:
    """Strip the ``\\n\\nFull log: ...`` suffix that thread-mode subagents append to results.

    ``Subagent._read_log()`` in ``types.py`` appends ``"\\n\\nFull log: {logdir}"``
    to every thread-mode result string. This suffix breaks JSON parsing when
    ``output_schema`` is set. This helper strips it so the caller sees clean content.
    """
    log_sep = "\n\nFull log: "
    if log_sep in text:
        return text.split(log_sep, 1)[0]
    return text


def _parse_result(result_dict: dict, output_schema: "type | dict | None") -> dict:
    """Parse a subagent result dict against an output_schema if provided.

    When output_schema is set and the result is a success with a JSON string,
    attempt to parse it. For Pydantic models, validate with model_validate().
    On parse failure, keep the raw string and add a "parse_error" key.

    Automatically strips the ``\\n\\nFull log: ...`` suffix added by
    ``Subagent._read_log()`` before parsing, so thread-mode subagent results
    with ``output_schema`` work correctly.

    Args:
        result_dict: Dict from subagent_wait() with "status" and "result" keys.
        output_schema: Optional Pydantic model class or type to parse against.

    Returns:
        Updated result dict. On successful parse the "result" value is the
        parsed object (dict for Pydantic, any JSON value otherwise).
    """
    if output_schema is None:
        return result_dict

    result_text = result_dict.get("result")
    if result_dict.get("status") != "success" or not result_text:
        return result_dict

    # Already parsed (e.g. subagent_wait() parsed it when output_schema is set on
    # the individual subagent); return as-is to avoid double-parsing.
    if not isinstance(result_text, str):
        return result_dict

    out = dict(result_dict)
    try:
        # Strip the log-path suffix that thread-mode _read_log() appends
        clean = _strip_log_suffix(result_text)
        parsed = json.loads(clean)
        if hasattr(output_schema, "model_validate"):
            out["result"] = output_schema.model_validate(parsed).model_dump()
        else:
            out["result"] = parsed
    except Exception as exc:
        logger.warning(f"output_schema parse failed: {exc}")
        out["parse_error"] = str(exc)
    return out


@dataclass
class BatchJob:
    """Manages a batch of subagents for parallel execution.

    Note: With the hook-based notification system, the orchestrator will receive
    completion messages automatically via the LOOP_CONTINUE hook. This class
    provides additional utilities for explicit synchronization when needed.
    """

    agent_ids: list[str]
    results: dict[str, ReturnType] = field(default_factory=dict)
    output_schema: "type | dict | None" = field(default=None)
    budget: SubagentBudget | None = field(default=None)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _budget_recorded_ids: set = field(default_factory=set, init=False, repr=False)

    def wait_all(
        self, timeout: int = 300, cancel_on_failure: bool = False
    ) -> dict[str, dict]:
        """Wait for all subagents to complete concurrently.

        Uses a thread pool to wait for all subagents simultaneously, so the
        wall-clock time is bounded by the slowest agent, not the sum of all
        agent times.

        When the ``BatchJob`` was created with an ``output_schema`` (via
        ``subagent_batch(output_schema=...)``) the results are automatically
        parsed through ``_parse_result()`` before being returned, matching the
        auto-parse behaviour of ``subagent_parallel(output_schema=...)``.

        Args:
            timeout: Maximum seconds to wait for all subagents
            cancel_on_failure: When True, cancel all remaining running subagents
                as soon as the first failure or timeout is detected. Cancelled
                agents are marked with ``status="failure"`` and
                ``result="Cancelled due to sibling failure"`` in the returned
                dict. For subprocess-mode agents this sends SIGTERM (fast); for
                thread-mode agents it marks the result immediately while the
                background thread continues until its next natural checkpoint.

                Note: regardless of this flag, any agents still running when the
                overall ``timeout`` expires are always cancelled so subprocess/ACP
                agents do not keep running after the caller has received timed-out
                results.

        Returns:
            Dict mapping agent_id to status dict. When ``output_schema`` is set,
            the ``"result"`` value is the parsed/validated object rather than a
            raw JSON string.
        """
        # cancel_event is set when we decide to bail early so that _wait_one
        # worker threads exit their poll loops promptly instead of blocking for
        # the full remaining timeout.
        cancel_event = threading.Event()
        # Poll subagent_wait() in short chunks so cancellation is responsive.
        _POLL_SECS = 5

        def _wait_one(agent_id: str, deadline: float) -> tuple[str, ReturnType]:
            import time

            while True:
                # Exit quickly when a sibling failure or overall timeout fired.
                if cancel_event.is_set():
                    with self._lock:
                        if agent_id in self.results:
                            return agent_id, self.results[agent_id]
                    return agent_id, ReturnType(
                        "cancelled", "Cancelled due to sibling failure"
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return agent_id, ReturnType(
                        "timeout", f"Timed out after {timeout}s"
                    )

                poll_secs = max(1, min(int(remaining), _POLL_SECS))
                try:
                    # Pass max_result_chars=0 so _parse_result() receives the full
                    # raw result without truncation. The default of 2000 would clip
                    # JSON from larger schemas and make structured output silently
                    # fail in _parse_result().
                    result = subagent_wait(
                        agent_id, timeout=poll_secs, max_result_chars=0
                    )
                except Exception as e:
                    logger.warning(f"Error waiting for {agent_id}: {e}")
                    return agent_id, ReturnType("failure", str(e))

                status = result.get("status", "failure")
                if status == "running":
                    # Agent still alive after this poll — loop and check again.
                    continue
                return agent_id, ReturnType(
                    status,
                    result.get("result"),
                    input_tokens=result.get("input_tokens"),
                    output_tokens=result.get("output_tokens"),
                )

        import time

        deadline = time.monotonic() + timeout
        # Safe default: if the try block raises before agent_to_future is built,
        # the post-cancel sweep below still has a valid (empty) dict to iterate.
        agent_to_future: dict[str, Future[tuple[str, ReturnType]]] = {}
        # Use explicit lifecycle instead of context manager so we can return
        # immediately after cancellation without blocking on still-running
        # _wait_one threads (ThreadPoolExecutor.__exit__ calls shutdown(wait=True)).
        pool = ThreadPoolExecutor(max_workers=len(self.agent_ids) or 1)
        try:
            futures = {
                pool.submit(_wait_one, aid, deadline): aid
                for aid in self.agent_ids
                # Re-wait agents whose previous wait_all() call timed out, or
                # whose post-cancel sweep left a synthetic "cancelled" placeholder
                # after the 0.5s grace window — both have output_tokens=None, so
                # skipping them would leave the budget undercounted when the agent
                # eventually finishes in a later call.
                if aid not in self.results
                or self.results[aid].status in ("timeout", "cancelled")
            }
            # Reverse lookup so the cancel_on_failure path can collect real
            # results from concurrently-completed futures before writing synthetic
            # placeholders (otherwise output_tokens can be silently lost).
            agent_to_future = {aid: f for f, aid in futures.items()}
            try:
                for future in as_completed(futures, timeout=timeout):
                    agent_id, result = future.result()
                    with self._lock:
                        prev = self.results.get(agent_id)
                        # Overwrite placeholder results (timeout, synthetic cancel)
                        # with the real result. Never overwrite an already-final
                        # non-placeholder result.
                        if prev is None or prev.status in ("timeout", "cancelled"):
                            self.results[agent_id] = result

                    # Cancel remaining agents on first failure/timeout
                    if cancel_on_failure and result.status in ("failure", "timeout"):
                        with self._lock:
                            remaining_ids = [
                                aid for aid in self.agent_ids if aid not in self.results
                            ]
                        for aid_to_cancel in remaining_ids:
                            try:
                                subagent_cancel(aid_to_cancel)
                            except ValueError:
                                pass  # Agent already finished
                            with self._lock:
                                if aid_to_cancel not in self.results:
                                    # Prefer the real result if the future has already
                                    # completed concurrently — preserves output_tokens
                                    # for accurate budget accounting instead of losing
                                    # them to a synthetic placeholder with None tokens.
                                    f = agent_to_future.get(aid_to_cancel)
                                    real: ReturnType | None = None
                                    if f is not None and f.done() and not f.cancelled():
                                        try:
                                            _, real = f.result()
                                        except Exception:
                                            pass
                                    self.results[aid_to_cancel] = real or ReturnType(
                                        "cancelled", "Cancelled due to sibling failure"
                                    )
                        # Signal workers to exit poll loops, then exit the loop.
                        cancel_event.set()
                        with self._lock:
                            if len(self.results) >= len(self.agent_ids):
                                break
            except FuturesTimeoutError:
                # as_completed timed out — mark any unfinished agents as timeout
                # and cancel them so subprocess/ACP agents don't keep running
                # after the caller has already received "timeout" results.
                timed_out_ids: list[str] = []
                for aid in futures.values():
                    with self._lock:
                        prev = self.results.get(aid)
                        # Only set/keep the timeout placeholder if no final result
                        # is stored yet (don't overwrite a real result that snuck in).
                        if prev is None or prev.status == "timeout":
                            self.results[aid] = ReturnType(
                                "timeout", f"Timed out after {timeout}s"
                            )
                            timed_out_ids.append(aid)
                for aid in timed_out_ids:
                    try:
                        subagent_cancel(aid)
                    except Exception:
                        pass
                cancel_event.set()
        finally:
            # Don't block on remaining _wait_one threads — all results are already
            # collected in self.results. Workers will exit their poll loops within
            # _POLL_SECS seconds once cancel_event is set.
            pool.shutdown(wait=False, cancel_futures=True)

        # Post-cancel sweep: upgrade synthetic "cancelled" placeholders with real
        # results from futures that completed after the cancel loop wrote the
        # placeholder.  This handles the race where:
        #   1. cancel loop checks f.done() → False, writes "cancelled" placeholder
        #   2. main loop breaks because all agent slots are filled
        #   3. _wait_one thread finishes shortly after (future becomes done)
        # Without this sweep the real result (which may carry output_tokens) is
        # discarded and the budget under-counts.
        if cancel_on_failure and agent_to_future:
            # Brief wait for _wait_one threads whose agents were just cancelled via
            # subagent_cancel().  After cancel, those threads typically finish within
            # microseconds (the cancel call unblocks their subagent_wait), but
            # pool.shutdown(wait=False) does not wait for them.  0.5s is generous.
            with self._lock:
                pending = [
                    agent_to_future[aid]
                    for aid, r in self.results.items()
                    if r.status == "cancelled" and aid in agent_to_future
                ]
            if pending:
                futures_wait(pending, timeout=0.5)
            for aid, f in agent_to_future.items():
                if f.done() and not f.cancelled():
                    with self._lock:
                        r = self.results.get(aid)
                    if r is not None and r.status == "cancelled":
                        try:
                            _, real = f.result()
                            with self._lock:
                                # Only replace if still the same placeholder (another
                                # concurrent wait_all() hasn't updated it first).
                                if self.results.get(aid) is r:
                                    self.results[aid] = real
                        except Exception:
                            pass

        raw = {aid: asdict(r) for aid, r in self.results.items()}

        # Record output tokens from completed agents into the shared budget.
        # Track per-agent IDs so repeated wait_all() calls (e.g. after a timeout
        # followed by a second wait) record newly-completed agents without
        # double-counting those already recorded in a prior call.
        if self.budget is not None:
            for aid, r in self.results.items():
                if aid not in self._budget_recorded_ids and r.output_tokens is not None:
                    self.budget.record(r.output_tokens)
                    self._budget_recorded_ids.add(aid)

        if self.output_schema is not None:
            return {aid: _parse_result(r, self.output_schema) for aid, r in raw.items()}
        return raw

    def is_complete(self) -> bool:
        """Check if all subagents have completed."""
        return len(self.results) == len(self.agent_ids)

    def total_tokens(self) -> dict[str, int | None]:
        """Return aggregated token counts across all completed subagents.

        Sums ``input_tokens`` and ``output_tokens`` from each completed result.
        Any subagent whose log has no usage metadata contributes ``None`` to its
        part — the aggregate is ``None`` when *no* completed subagent has token
        data, otherwise it is the sum of available counts.

        Returns:
            Dict with keys ``"input_tokens"`` and ``"output_tokens"``.
            Values are integers (sum of available counts) or ``None`` when no
            usage metadata was found in any completed subagent's log.

        Example::

            job = subagent_batch([("a", "task A"), ("b", "task B")])
            results = job.wait_all()
            stats = job.total_tokens()
            print(f"Tokens used: {stats['input_tokens']} in / {stats['output_tokens']} out")
        """
        with self._lock:
            completed = list(self.results.values())

        total_in: int | None = None
        total_out: int | None = None
        for r in completed:
            if r.input_tokens is not None:
                total_in = (total_in or 0) + r.input_tokens
            if r.output_tokens is not None:
                total_out = (total_out or 0) + r.output_tokens
        return {"input_tokens": total_in, "output_tokens": total_out}

    def get_completed(self) -> dict[str, dict]:
        """Get results of completed subagents so far.

        When the ``BatchJob`` was created with an ``output_schema`` (via
        ``subagent_batch(output_schema=...)``) the results are automatically
        parsed through ``_parse_result()`` before being returned, matching the
        behaviour of ``wait_all()``.
        """
        from dataclasses import asdict

        with self._lock:
            raw = {aid: asdict(r) for aid, r in self.results.items()}
            if self.output_schema is not None:
                return {
                    aid: _parse_result(r, self.output_schema) for aid, r in raw.items()
                }
            return raw

    def wait_any(self, timeout: int = 300) -> tuple[str, dict]:
        """Wait for the first subagent to complete and return its result.

        Useful for speculative/hedging patterns: spawn N subagents and take
        whichever finishes first, then cancel the rest.

        Args:
            timeout: Maximum seconds to wait for any agent to complete.

        Returns:
            Tuple of ``(agent_id, result_dict)`` for the first agent that
            completes. When ``output_schema`` is set on the ``BatchJob`` the
            result is automatically parsed.

        Raises:
            TimeoutError: If no agent completes within ``timeout`` seconds.

        Example::

            job = subagent_batch([
                ("attempt-fast", "Try the quick approach for task X"),
                ("attempt-thorough", "Try the thorough approach for task X"),
            ])
            first_id, result = job.wait_any(timeout=120)
            print(f"{first_id} finished first: {result['status']}")
            # Cancel the remaining agent
            from gptme.tools.subagent import subagent_cancel
            for aid in job.agent_ids:
                if aid != first_id:
                    subagent_cancel(aid)
        """
        done_event = threading.Event()
        first_result: list[tuple[str, ReturnType]] = []

        terminal_statuses = {"success", "failure", "clarification_needed"}
        deadline = time.monotonic() + timeout

        def _wait_one_notify(agent_id: str) -> None:
            if done_event.is_set():
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return

            try:
                raw = subagent_wait(
                    agent_id, timeout=max(1, math.ceil(remaining)), max_result_chars=0
                )
                status = raw.get("status", "failure")
                result = ReturnType(
                    status,
                    raw.get("result"),
                    input_tokens=raw.get("input_tokens"),
                    output_tokens=raw.get("output_tokens"),
                )
            except Exception as exc:
                result = ReturnType("failure", str(exc))

            if result.status not in terminal_statuses:
                return

            with self._lock:
                if agent_id not in self.results:
                    self.results[agent_id] = result
                # Signal if this is the first successful/failed terminal result
                if not done_event.is_set():
                    first_result.append((agent_id, result))
                    done_event.set()

        # If any result is already cached, return it immediately.
        with self._lock:
            for aid in self.agent_ids:
                if aid in self.results:
                    raw = asdict(self.results[aid])
                    if self.output_schema is not None:
                        raw = _parse_result(raw, self.output_schema)
                    return aid, raw
            pending = [aid for aid in self.agent_ids if aid not in self.results]

        threads = [
            threading.Thread(target=_wait_one_notify, args=(aid,), daemon=True)
            for aid in pending
        ]
        for t in threads:
            t.start()

        signalled = done_event.wait(timeout=timeout)
        if not signalled or not first_result:
            raise TimeoutError(
                f"No subagent completed within {timeout}s (waiting for: {pending})"
            )

        first_id, first_ret = first_result[0]
        raw = asdict(first_ret)
        if self.output_schema is not None:
            raw = _parse_result(raw, self.output_schema)
        return first_id, raw


def subagent_batch(
    tasks: list[tuple[str, str]],
    use_subprocess: bool = False,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    model: str | None = None,
    profile: str | None = None,
    isolated: bool = False,
    output_schema: "type | dict | None" = None,
    workdir: str | Path | None = None,
    context_turns: int | None = None,
    context_window: int | None = None,
    redact_secrets: bool = True,
    budget: SubagentBudget | None = None,
) -> BatchJob:
    """Start multiple subagents in parallel and return a BatchJob to manage them.

    This is a convenience function for fire-and-gather patterns where you want
    to run multiple independent tasks concurrently.

    With the hook-based notification system, completion messages are delivered
    automatically via the LOOP_CONTINUE hook. The BatchJob provides additional
    utilities for explicit synchronization when needed.

    Args:
        tasks: List of (agent_id, prompt) tuples
        use_subprocess: If True, run subagents in subprocesses for output isolation
        use_acp: If True, run subagents via ACP protocol
        acp_command: ACP agent command (default: "gptme-acp")
        model: Model override applied to every subagent.
        profile: Agent profile name applied to every subagent.
        isolated: If True, run each subagent in its own git worktree so file
            edits don't conflict between agents or with the parent.
        output_schema: Optional Pydantic model class. When set, subagents are
            instructed to return JSON matching the schema in their complete block.
            Results are automatically parsed when ``wait_all()`` is called — the
            ``"result"`` value in each returned dict will be the parsed/validated
            object rather than a raw JSON string, matching the behaviour of
            ``subagent_parallel(output_schema=...)``.
        workdir: Working directory passed to every subagent. Useful when running
            subagents against a specific project directory.
        context_turns: Number of recent parent conversation turns to forward to
            each subagent as context prefix. Pass ``None`` (default) to use no
            parent context.
        context_window: Limit workspace context messages passed to each subagent.
            Pass ``0`` for strongest isolation (subagent sees only agent identity
            and tools, no workspace files). Pass ``None`` (default) for the full
            inherited workspace context. Only applies to thread-mode subagents.
        redact_secrets: If True (default), redact secrets from workspace context
            passed to subagents. Pass False only if you need subagents to see
            config values that are incorrectly flagged as secrets.

    Returns:
        A BatchJob instance for managing the parallel subagents.
        The BatchJob provides wait_all(timeout) to wait for completion,
        is_complete() to check status, and get_completed() for partial results.

    Example::

        job = subagent_batch([
            ("impl", "Implement feature X"),
            ("test", "Write tests for feature X"),
            ("docs", "Document feature X"),
        ])
        # Orchestrator continues with other work...
        # Completion messages delivered via LOOP_CONTINUE hook:
        #   "✅ Subagent 'impl' completed: Feature implemented"
        #   "✅ Subagent 'test' completed: 5 tests added"
        #
        # Or explicitly wait for all if needed:
        results = job.wait_all(timeout=300)
    """
    job = BatchJob(
        agent_ids=[t[0] for t in tasks], output_schema=output_schema, budget=budget
    )

    # Start subagents, skipping any after the budget is exhausted
    started: list[str] = []
    for agent_id, prompt in tasks:
        if budget is not None and budget.exhausted():
            logger.debug(
                "subagent_batch: budget exhausted, skipping agent '%s'", agent_id
            )
            with job._lock:
                job.results[agent_id] = ReturnType(
                    "budget_exceeded", "Budget exhausted before this agent could start"
                )
            continue
        subagent(
            agent_id=agent_id,
            prompt=prompt,
            use_subprocess=use_subprocess,
            use_acp=use_acp,
            acp_command=acp_command,
            model=model,
            profile=profile,
            isolated=isolated,
            output_schema=output_schema,
            workdir=workdir,
            context_turns=context_turns,
            context_window=context_window,
            redact_secrets=redact_secrets,
        )
        started.append(agent_id)

    logger.info(f"Started batch of {len(started)} subagents: {started}")
    return job


def subagent_parallel(
    tasks: list[tuple[str, str]],
    timeout: int = 300,
    max_concurrent: int | None = None,
    use_subprocess: bool = False,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    model: str | None = None,
    profile: str | None = None,
    isolated: bool = False,
    isolation: Literal["worktree"] | None = None,
    output_schema: "type | dict | None" = None,
    workdir: str | Path | None = None,
    context_turns: int | None = None,
    context_window: int | None = None,
    redact_secrets: bool = True,
    cancel_on_failure: bool = False,
    budget: SubagentBudget | None = None,
) -> list[dict]:
    """Fan out N subagents in parallel, wait for all, return results as an ordered list.

    This is the simplest way to run independent tasks concurrently and collect
    all results. Unlike ``subagent_batch()``, this function blocks until every
    subagent has finished (or timed out) and returns the results in the same
    order as the input tasks.

    Waits for all subagents concurrently — wall-clock time is bounded by the
    slowest agent, not the sum of all agent times.

    Args:
        tasks: List of ``(agent_id, prompt)`` tuples. Each agent_id must be
            unique within this call.
        timeout: Maximum seconds to wait for all subagents to finish. Agents
            that exceed this deadline are reported with status ``"timeout"``.
        max_concurrent: Maximum number of subagents to run at the same time.
            When set, excess tasks are queued and spawned as earlier agents
            complete — this never raises an error, it only limits concurrency.
            Pass ``None`` (default) to spawn all tasks simultaneously.
            Useful for large fan-outs where running too many agents at once
            would exhaust system resources or API rate limits.
        use_subprocess: If True, run each subagent in a subprocess for output
            isolation. Subprocess mode captures stdout/stderr separately and
            supports hard-kill on timeout.
        use_acp: If True, run each subagent via the ACP protocol.
        acp_command: ACP agent command (default: "gptme-acp"). Only used when
            ``use_acp=True``.
        model: Model override applied to every subagent. Pass ``None`` to
            inherit the parent's model.
        profile: Agent profile name applied to every subagent (e.g.
            ``"explorer"``, ``"developer"``, ``"verifier"``).
        isolated: If True, run each subagent in its own git worktree so file
            edits don't conflict between agents or with the parent.
            Prefer ``isolation="worktree"`` for new code.
        isolation: String-based isolation mode. Use ``"worktree"`` to give each
            subagent its own git worktree. On completion, worktrees with no
            local changes are auto-removed; worktrees with commits ahead of
            HEAD have their branch preserved (reported in the result) for
            the caller to inspect or merge.
        output_schema: Optional Pydantic model class. When set, subagents are
            instructed to return valid JSON matching the schema in their
            ``complete`` block. Results are automatically parsed: on success the
            ``"result"`` value is the parsed/validated object (a dict for Pydantic
            models) rather than a raw JSON string. A ``"parse_error"`` key is
            added to any result that cannot be parsed.
        workdir: Working directory passed to every subagent. Useful when running
            subagents against a specific project directory.
        context_turns: Number of recent parent conversation turns to forward to
            each subagent as context prefix. Pass ``None`` (default) to use no
            parent context.
        context_window: Limit workspace context messages passed to each subagent.
            Pass ``0`` for strongest isolation (subagent sees only agent identity
            and tools, no workspace files). Pass ``None`` (default) for the full
            inherited workspace context. Only applies to thread-mode subagents.
        redact_secrets: If True (default), scrub common secret patterns from
            workspace context before passing it to subagents.
        cancel_on_failure: When True, cancel all remaining running subagents
            as soon as the first failure or timeout is detected. Cancelled
            agents are reported with ``status="failure"`` and
            ``result="Cancelled due to sibling failure"``. For subprocess-mode
            agents this sends SIGTERM (fast); for thread-mode agents the result
            is marked immediately while the background thread finishes naturally.

            Use this for defensive fan-out orchestration where one failing
            subagent means the overall task has failed and continuing would
            waste resources. Equivalent to calling ``subagent_cancel()``
            manually after ``subagent_wait_any()`` detects a failure, but
            handled automatically inside the parallel wait.
        budget: Optional shared token budget. When set, each agent's output
            tokens are recorded after it completes, and any agent whose spawn
            is attempted after the budget is exhausted is returned immediately
            with ``status="budget_exceeded"`` without being started. Agents
            already running when the budget hits zero are allowed to finish
            normally. Pass a ``SubagentBudget`` to share a budget across
            multiple ``subagent_parallel()`` calls (dynamic fan-out loop).

    Returns:
        List of result dicts in the same order as ``tasks``. Each dict has
        ``"status"`` (``"success"`` / ``"failure"`` / ``"timeout"`` /
        ``"budget_exceeded"``) and ``"result"`` (parsed object when
        ``output_schema`` is set, else the summary text from the subagent's
        ``complete`` block).

    Example::

        # Process three independent tasks in parallel
        results = subagent_parallel([
            ("researcher", "Research the top 5 Python async frameworks"),
            ("coder",      "Implement a basic async HTTP client"),
            ("tester",     "Write pytest tests for an async HTTP client"),
        ])
        for (agent_id, _), result in zip(tasks, results):
            print(f"{agent_id}: {result['status']} — {result['result'][:80]}")

        # With worktree isolation for concurrent file edits (string API)
        results = subagent_parallel(
            [("fix-a", "Fix bug in module A"), ("fix-b", "Fix bug in module B")],
            isolation="worktree",
        )

        # Fail-fast: cancel the fleet when the first subagent fails
        results = subagent_parallel(
            [("verifier-a", "Verify output A"), ("verifier-b", "Verify output B")],
            cancel_on_failure=True,
        )
        if any(r["status"] == "failure" for r in results):
            print("Verification failed — remaining agents were cancelled")

        # With structured output (Pydantic model)
        from pydantic import BaseModel

        class AnalysisResult(BaseModel):
            summary: str
            score: int
            issues: list[str]

        results = subagent_parallel(
            [("a1", "Analyze module A"), ("a2", "Analyze module B")],
            output_schema=AnalysisResult,
        )
        for r in results:
            if r["status"] == "success":
                analysis = r["result"]  # already a validated dict
                print(f"Score: {analysis['score']}, Issues: {analysis['issues']}")

        # Budget-aware dynamic loop (spawn until budget runs out)
        from gptme.tools.subagent import SubagentBudget
        budget = SubagentBudget(total=200_000)
        while not budget.exhausted():
            results = subagent_parallel(next_batch, budget=budget)
            # items skipped after budget exhaustion have status="budget_exceeded"

        # Fleet cap: run at most 4 agents at a time, queueing the rest
        results = subagent_parallel(
            [("task-1", "..."), ("task-2", "..."), ("task-3", "..."), ("task-4", "..."),
             ("task-5", "..."), ("task-6", "...")],
            max_concurrent=4,
        )
        # task-5 and task-6 start only after earlier slots free up
    """
    if not tasks:
        return []

    if max_concurrent is not None and max_concurrent < 1:
        # Treat non-positive as uncapped (consistent with docstring: "never raises an error")
        max_concurrent = None

    agent_ids = [aid for aid, _ in tasks]
    if len(agent_ids) != len(set(agent_ids)):
        raise ValueError(
            "Task agent_ids must be unique within a subagent_parallel() call"
        )

    # When max_concurrent is set, use a ThreadPoolExecutor where each worker
    # does spawn+wait — this naturally queues excess tasks and respects the cap.
    if max_concurrent is not None:
        return _subagent_parallel_capped(
            tasks=tasks,
            max_concurrent=max_concurrent,
            timeout=timeout,
            use_subprocess=use_subprocess,
            use_acp=use_acp,
            acp_command=acp_command,
            model=model,
            profile=profile,
            isolated=isolated,
            isolation=isolation,
            output_schema=output_schema,
            workdir=workdir,
            context_turns=context_turns,
            context_window=context_window,
            redact_secrets=redact_secrets,
            cancel_on_failure=cancel_on_failure,
            budget=budget,
        )

    # Track which agent_ids were skipped due to budget exhaustion vs actually started.
    started_ids: list[str] = []
    budget_exceeded_ids: set[str] = set()

    try:
        for agent_id, prompt in tasks:
            if budget is not None and budget.exhausted():
                budget_exceeded_ids.add(agent_id)
                logger.debug(
                    "subagent_parallel: budget exhausted, skipping agent '%s'", agent_id
                )
                continue
            subagent(
                agent_id=agent_id,
                prompt=prompt,
                use_subprocess=use_subprocess,
                use_acp=use_acp,
                acp_command=acp_command,
                model=model,
                profile=profile,
                isolated=isolated,
                isolation=isolation,
                output_schema=output_schema,
                workdir=workdir,
                context_turns=context_turns,
                context_window=context_window,
                redact_secrets=redact_secrets,
            )
            started_ids.append(agent_id)
    except Exception:
        for aid in started_ids:
            try:
                subagent_cancel(aid)
            except Exception:
                pass
        raise

    logger.info(
        "subagent_parallel: started %d subagents%s",
        len(started_ids),
        f", skipped {len(budget_exceeded_ids)} (budget exhausted)"
        if budget_exceeded_ids
        else "",
    )

    # Collect results from started agents in parallel.
    # Pass budget so wait_all() records tokens once via BatchJob._budget_recorded_ids,
    # which prevents double-counting on repeated wait_all() calls.
    job = BatchJob(agent_ids=started_ids, budget=budget)
    if started_ids:
        job.wait_all(timeout=timeout, cancel_on_failure=cancel_on_failure)

    # Build ordered results, substituting budget_exceeded for skipped agents
    _budget_exceeded_result = ReturnType(
        "budget_exceeded", "Budget exhausted before this agent could start"
    )
    raw_results = [
        asdict(
            _budget_exceeded_result
            if agent_id in budget_exceeded_ids
            else job.results.get(
                agent_id, ReturnType("failure", "No result (timeout or missing)")
            )
        )
        for agent_id, _ in tasks
    ]
    if output_schema is not None:
        return [_parse_result(r, output_schema) for r in raw_results]
    return raw_results


def _subagent_parallel_capped(
    tasks: list[tuple[str, str]],
    max_concurrent: int,
    timeout: int,
    use_subprocess: bool,
    use_acp: bool,
    acp_command: str,
    model: str | None,
    profile: str | None,
    isolated: bool,
    isolation: "Literal['worktree'] | None",
    output_schema: "type | dict | None",
    workdir: "str | Path | None",
    context_turns: "int | None",
    context_window: "int | None",
    redact_secrets: bool,
    cancel_on_failure: bool,
    budget: "SubagentBudget | None",
) -> list[dict]:
    """Spawn+wait each task with at most max_concurrent running simultaneously.

    Uses a ThreadPoolExecutor so excess tasks are naturally queued.
    Each worker acquires a slot, checks the budget, spawns, waits, records tokens.
    """
    deadline = time.monotonic() + timeout
    results_map: dict[str, dict] = {}
    results_lock = threading.Lock()
    cancel_event = threading.Event()
    # Track agents currently blocked in subagent_wait() so we can cancel them
    # immediately when cancel_on_failure fires, rather than waiting for their
    # natural timeout.
    inflight_ids: set[str] = set()
    inflight_lock = threading.Lock()
    # Tracks which agent_ids have had budget.record() called inside run_one.
    # The timeout handler needs this to avoid skipping budget for agents that
    # completed on the boundary (run_one skips record when _timed_out is True,
    # but f.done() futures still have their results returned to the caller).
    budget_recorded_ids: set[str] = set()

    _budget_exceeded = asdict(
        ReturnType("budget_exceeded", "Budget exhausted before this agent could start")
    )

    def run_one(agent_id: str, prompt: str) -> tuple[str, dict]:
        # Fast-exit if a sibling failure triggered cancellation.
        if cancel_event.is_set():
            return agent_id, asdict(
                ReturnType("cancelled", "Cancelled due to sibling failure")
            )

        # Budget check at the moment this slot is acquired.
        if budget is not None and budget.exhausted():
            return agent_id, _budget_exceeded

        remaining_secs = max(1, int(deadline - time.monotonic()))
        if remaining_secs <= 0:
            return agent_id, asdict(
                ReturnType("timeout", f"Timed out after {timeout}s")
            )

        try:
            subagent(
                agent_id=agent_id,
                prompt=prompt,
                use_subprocess=use_subprocess,
                use_acp=use_acp,
                acp_command=acp_command,
                model=model,
                profile=profile,
                isolated=isolated,
                isolation=isolation,
                output_schema=output_schema,
                workdir=workdir,
                context_turns=context_turns,
                context_window=context_window,
                redact_secrets=redact_secrets,
            )
        except Exception as exc:
            # cancel_on_failure: sweep siblings even on spawn failure
            if cancel_on_failure:
                cancel_event.set()
                with inflight_lock:
                    for sibling_id in list(inflight_ids):
                        try:
                            subagent_cancel(sibling_id)
                        except Exception:
                            pass
            return agent_id, asdict(ReturnType("failure", str(exc)))

        with inflight_lock:
            inflight_ids.add(agent_id)

        # Race guard: cancel_on_failure can fire between the initial cancel_event
        # check (above) and this registration. If so, the canceller already walked
        # inflight_ids without seeing us. Cancel ourselves before entering the wait.
        if cancel_event.is_set():
            with inflight_lock:
                inflight_ids.discard(agent_id)
            try:
                subagent_cancel(agent_id)
            except Exception:
                pass
            return agent_id, asdict(
                ReturnType("cancelled", "Cancelled due to sibling failure")
            )

        try:
            result = subagent_wait(agent_id, timeout=remaining_secs, max_result_chars=0)
        except Exception as exc:
            # cancel_on_failure: sweep siblings even when subagent_wait() raises.
            # Self is removed from inflight_ids by the finally block below.
            if cancel_on_failure:
                cancel_event.set()
                with inflight_lock:
                    for sibling_id in list(inflight_ids):
                        try:
                            subagent_cancel(sibling_id)
                        except Exception:
                            pass
            return agent_id, asdict(ReturnType("failure", str(exc)))
        finally:
            with inflight_lock:
                inflight_ids.discard(agent_id)

        # Record output tokens in the shared budget.
        # Skip on the overall-timeout path (_timed_out=True): pool.shutdown(wait=False)
        # means this thread may still be executing after _subagent_parallel_capped()
        # has returned. A post-return budget.record() would corrupt the caller's
        # next-batch spawn decisions. This guard makes the invariant unconditional;
        # tokens from timed-out agents are not counted (they never completed usefully).
        # Agents that finish on the boundary (f.done() in the timeout handler) are
        # recorded there instead, using budget_recorded_ids to avoid double-counting.
        if budget is not None and not _timed_out:
            out_tok = result.get("output_tokens")
            if out_tok is not None:
                budget.record(out_tok)
                budget_recorded_ids.add(agent_id)

        # Trigger cancellation if this agent failed and fail-fast is on.
        # Cancel siblings currently blocked in subagent_wait() so they stop
        # promptly rather than running until their natural timeout.
        if cancel_on_failure and result.get("status") in ("failure", "timeout"):
            cancel_event.set()
            with inflight_lock:
                for sibling_id in list(inflight_ids):
                    try:
                        subagent_cancel(sibling_id)
                    except Exception:
                        pass

        return agent_id, result

    pool = ThreadPoolExecutor(max_workers=max_concurrent)
    _timed_out = False
    try:
        futures: dict[Future[tuple[str, dict]], str] = {
            pool.submit(run_one, aid, prompt): aid for aid, prompt in tasks
        }
        try:
            for future in as_completed(futures, timeout=timeout):
                aid, result = future.result()
                with results_lock:
                    results_map[aid] = result
        except FuturesTimeoutError:
            _timed_out = True
            # Stop queued workers from starting new agents and cancel in-flight ones.
            cancel_event.set()
            with inflight_lock:
                for sibling_id in list(inflight_ids):
                    try:
                        subagent_cancel(sibling_id)
                    except Exception:
                        pass
            for f in futures:
                f.cancel()
            for f, aid in futures.items():
                with results_lock:
                    if aid in results_map:
                        continue
                    if f.cancelled():
                        results_map[aid] = asdict(
                            ReturnType("cancelled", "Cancelled due to overall timeout")
                        )
                    elif f.done():
                        # Completed on the timeout boundary but wasn't yielded by as_completed.
                        # f.result() acts as a memory barrier: any writes in run_one (including
                        # budget_recorded_ids.add) are visible here after this call returns.
                        try:
                            _, result = f.result()
                        except Exception as exc:
                            result = asdict(ReturnType("failure", str(exc)))
                        # run_one skips budget.record() when _timed_out is True, but this
                        # result is being returned to the caller, so record the tokens now
                        # to keep the budget accurate for subsequent batches.
                        if budget is not None and aid not in budget_recorded_ids:
                            out_tok = result.get("output_tokens")
                            if out_tok is not None:
                                budget.record(out_tok)
                        results_map[aid] = result
                    else:
                        results_map[aid] = asdict(
                            ReturnType("timeout", f"Timed out after {timeout}s")
                        )
    finally:
        # On timeout, don't block on shutdown: subagent_cancel() signals running agents
        # to stop, but we don't wait for threads blocked in subagent_wait() to return.
        # cancel_futures=True also discards any queued-but-not-yet-started workers.
        # Budget recording in run_one is guarded by _timed_out so abandoned threads
        # cannot mutate the shared budget after this function returns.
        pool.shutdown(wait=not _timed_out, cancel_futures=_timed_out)

    raw_results = [
        results_map.get(
            aid, asdict(ReturnType("failure", "No result (timeout or missing)"))
        )
        for aid, _ in tasks
    ]
    if output_schema is not None:
        return [_parse_result(r, output_schema) for r in raw_results]
    return raw_results


def subagent_pipeline(
    items: list[tuple[str, str]],
    *stages: Callable[[str, str], str],
    timeout: float = 600,
    use_subprocess: bool = False,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    model: str | None = None,
    profile: str | None = None,
    isolated: bool = False,
    output_schema: "type | dict | None" = None,
    workdir: str | Path | None = None,
    context_turns: int | None = None,
    context_window: int | None = None,
    redact_secrets: bool = True,
    budget: SubagentBudget | None = None,
) -> list[list[dict]]:
    """Process items through multiple stages with no barrier between stages.

    Each item is processed through all stages sequentially. Items at different
    stages run concurrently — item A can be in stage 2 while item B is still
    in stage 1. This is the "pipeline" pattern as opposed to repeated
    subagent_parallel() calls which add a full barrier between stages.

    Wall-clock time is bounded by the slowest single-item chain, not the sum
    of the slowest per-stage.

    Args:
        items: List of ``(agent_id_prefix, initial_prompt)`` tuples.
        *stages: Callables of the form ``stage(item_prompt, prev_result) -> str``
            where ``item_prompt`` is the original item prompt and ``prev_result``
            is the raw result text from the previous stage (empty string for the
            first stage). Each callable returns the prompt to use for the next
            subagent in the chain.
        timeout: Maximum seconds to wait for the entire pipeline to finish.
        use_subprocess: If True, run each subagent in a subprocess.
        use_acp: If True, run each subagent via the ACP protocol.
        acp_command: ACP agent command (default: "gptme-acp"). Only used when
            ``use_acp=True``.
        model: Model override applied to every subagent.
        profile: Agent profile name applied to every subagent.
        isolated: If True, run each subagent in its own git worktree.
        output_schema: Optional Pydantic model class. When set, each final-stage
            subagent is instructed to return JSON matching the schema and results
            are automatically parsed.
        workdir: Working directory passed to every subagent.
        context_turns: Number of recent parent turns to forward to each subagent.
        context_window: Limit workspace context messages passed to each subagent.
            Pass ``0`` for strongest isolation (subagent sees only agent identity
            and tools, no workspace files). Pass ``None`` (default) for the full
            inherited workspace context. Only applies to thread-mode subagents.
        redact_secrets: If True (default), redact secrets from workspace context.

    Returns:
        List of lists of result dicts. ``results[i][j]`` is the result dict for
        item ``i`` at stage ``j``. Each dict has ``"status"`` and ``"result"``
        keys (plus ``"input_tokens"`` / ``"output_tokens"`` when available).
        When ``output_schema`` is set, the final-stage ``"result"`` value is the
        parsed/validated object rather than a raw JSON string.

    Example::

        # Two-stage review pipeline: find issues, then verify each finding
        results = subagent_pipeline(
            [("file-auth", "Review auth.py"), ("file-db", "Review db.py")],
            # Stage 0: review
            lambda item, _: f"Review this file for bugs: {item}",
            # Stage 1: verify each review finding
            lambda item, prev: (
                f"Adversarially verify each finding in this review:\\n{prev}\\n"
                f"Original file to review: {item}"
            ),
        )
        # file-auth advances to stage 1 as soon as its stage 0 completes,
        # while file-db may still be in stage 0.
        for (prefix, _), stage_results in zip(items, results):
            final = stage_results[-1]
            print(f"{prefix}: {final['status']} — {final['result'][:80]}")

        # With isolated worktrees so concurrent file edits don't conflict
        results = subagent_pipeline(
            [("impl-a", "Implement feature A"), ("impl-b", "Implement feature B")],
            lambda item, _: item,
            lambda item, prev: f"Write tests for: {prev}",
            isolated=True,
        )
    """
    if not items:
        return []
    if not stages:
        raise ValueError("subagent_pipeline requires at least one stage")

    # Per-item results: results[item_idx][stage_idx] = result dict
    all_results: list[list[dict | None]] = [[None] * len(stages) for _ in items]
    results_lock = threading.Lock()
    wait_timeout = max(1, int(timeout))

    def process_item(item_idx: int, item_id_prefix: str, item_prompt: str) -> None:
        prev_result_text = ""
        for stage_idx, stage_fn in enumerate(stages):
            agent_id = f"{item_id_prefix}-s{stage_idx}"
            # Only pass output_schema to the final stage
            is_final = stage_idx == len(stages) - 1

            # Budget check: skip remaining stages if budget is exhausted.
            # Note: enforcement near exhaustion is best-effort in concurrent pipelines.
            # Another thread may record tokens and exhaust the budget between this check
            # and the subagent() call below. The violation is bounded (at most one extra
            # agent per concurrent item thread). A hard atomic reserve is not possible
            # without knowing the token cost upfront.
            if budget is not None and budget.exhausted():
                logger.debug(
                    "subagent_pipeline: budget exhausted, skipping '%s'", agent_id
                )
                with results_lock:
                    for remaining in range(stage_idx, len(stages)):
                        all_results[item_idx][remaining] = {
                            "status": "budget_exceeded",
                            "result": "Budget exhausted before this stage could start",
                        }
                return

            try:
                stage_prompt = stage_fn(item_prompt, prev_result_text)
                subagent(
                    agent_id=agent_id,
                    prompt=stage_prompt,
                    use_subprocess=use_subprocess,
                    use_acp=use_acp,
                    acp_command=acp_command,
                    model=model,
                    profile=profile,
                    isolated=isolated,
                    output_schema=output_schema if is_final else None,
                    workdir=workdir,
                    context_turns=context_turns,
                    context_window=context_window,
                    redact_secrets=redact_secrets,
                )
                result = subagent_wait(
                    agent_id,
                    timeout=wait_timeout,
                    max_result_chars=0,  # full result for schema parsing
                )
            except Exception as exc:
                result = {"status": "failure", "result": str(exc)}

            # Record output tokens in the shared budget.
            # When output_tokens is absent (exception path or "running" result),
            # fall back to reading the agent's log directly so that tokens spent
            # by an already-started agent are not silently lost.
            if budget is not None:
                out_tok = result.get("output_tokens")
                if out_tok is not None:
                    budget.record(out_tok)
                else:
                    try:
                        from .types import _subagents, _subagents_lock

                        with _subagents_lock:
                            # Use the last match — _subagents is append-only so the
                            # most recently spawned entry for this agent_id is last.
                            # next() with a forward iterator would return the oldest
                            # (stale) entry when the same id is reused across runs.
                            sa = next(
                                (
                                    s
                                    for s in reversed(_subagents)
                                    if s.agent_id == agent_id
                                ),
                                None,
                            )
                        if sa:
                            _, fb_out = sa._read_token_stats()
                            if fb_out is not None:
                                budget.record(fb_out)
                    except Exception:
                        pass

            with results_lock:
                all_results[item_idx][stage_idx] = result
            if result.get("status") != "success":
                # On failure/budget_exceeded, mark remaining stages as skipped
                with results_lock:
                    for remaining in range(stage_idx + 1, len(stages)):
                        all_results[item_idx][remaining] = {
                            "status": "skipped",
                            "result": f"Skipped: stage {stage_idx} failed",
                        }
                return
            prev_result_text = result.get("result") or ""
            if "\n\nFull log: " in prev_result_text:
                prev_result_text = prev_result_text.split("\n\nFull log: ", 1)[0]

    threads = [
        threading.Thread(
            target=process_item,
            args=(idx, item_id, item_prompt),
            daemon=True,
        )
        for idx, (item_id, item_prompt) in enumerate(items)
    ]
    deadline = time.monotonic() + timeout
    for t in threads:
        t.start()
    for t in threads:
        remaining = max(0.0, deadline - time.monotonic())
        t.join(timeout=remaining)

    with results_lock:
        # Fill any missing results (thread timed out before join). Threads may
        # continue running after this point, so return a deep-copied snapshot
        # rather than the mutable worker-owned result matrix.
        for item_idx in range(len(items)):
            for stage_idx in range(len(stages)):
                if all_results[item_idx][stage_idx] is None:
                    all_results[item_idx][stage_idx] = {
                        "status": "timeout",
                        "result": f"Pipeline timed out after {timeout}s",
                    }

        # Parse final-stage results against output_schema if provided
        if output_schema is not None:
            for item_idx in range(len(items)):
                final_idx = len(stages) - 1
                all_results[item_idx][final_idx] = _parse_result(
                    all_results[item_idx][final_idx] or {}, output_schema
                )

        return copy.deepcopy(cast(list[list[dict]], all_results))
