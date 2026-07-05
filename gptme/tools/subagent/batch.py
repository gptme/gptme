"""Subagent batch execution — parallel task management.

Provides BatchJob for managing groups of subagents and subagent_batch()
for convenient fire-and-gather patterns. Also provides subagent_parallel()
for a simpler synchronous fan-out pattern.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .api import subagent, subagent_cancel, subagent_wait
from .types import ReturnType

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


def _parse_result(result_dict: dict, output_schema: type | None) -> dict:
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
    output_schema: type | None = field(default=None)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait_all(self, timeout: int = 300) -> dict[str, dict]:
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

        Returns:
            Dict mapping agent_id to status dict. When ``output_schema`` is set,
            the ``"result"`` value is the parsed/validated object rather than a
            raw JSON string.
        """

        def _wait_one(agent_id: str, deadline: float) -> tuple[str, ReturnType]:
            import time

            remaining = max(1, int(deadline - time.monotonic()))
            try:
                # Pass max_result_chars=0 so _parse_result() receives the full
                # raw result without truncation. The default of 2000 would clip
                # JSON from larger schemas and make structured output silently
                # fail in _parse_result().
                result = subagent_wait(agent_id, timeout=remaining, max_result_chars=0)
                status = result.get("status", "failure")
                # subagent_wait() returns a non-terminal "running" status (with
                # result=None) when a thread/ACP agent is still alive after the
                # wait — these can't be force-killed. Report that as "timeout"
                # per the documented contract, rather than leaking
                # status="running"/result=None, which breaks callers that index
                # into result (e.g. this function's own docstring example).
                if status == "running":
                    return agent_id, ReturnType(
                        "timeout", f"Timed out after {timeout}s"
                    )
                return agent_id, ReturnType(status, result.get("result"))
            except Exception as e:
                logger.warning(f"Error waiting for {agent_id}: {e}")
                return agent_id, ReturnType("failure", str(e))

        import time

        deadline = time.monotonic() + timeout
        with ThreadPoolExecutor(max_workers=len(self.agent_ids) or 1) as pool:
            futures = {
                pool.submit(_wait_one, aid, deadline): aid
                for aid in self.agent_ids
                if aid not in self.results
            }
            try:
                for future in as_completed(futures, timeout=timeout):
                    agent_id, result = future.result()
                    with self._lock:
                        if agent_id not in self.results:
                            self.results[agent_id] = result
            except FuturesTimeoutError:
                # as_completed timed out — mark any unfinished agents
                for aid in futures.values():
                    with self._lock:
                        if aid not in self.results:
                            self.results[aid] = ReturnType(
                                "timeout", f"Timed out after {timeout}s"
                            )

        raw = {aid: asdict(r) for aid, r in self.results.items()}
        if self.output_schema is not None:
            return {aid: _parse_result(r, self.output_schema) for aid, r in raw.items()}
        return raw

    def is_complete(self) -> bool:
        """Check if all subagents have completed."""
        return len(self.results) == len(self.agent_ids)

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


def subagent_batch(
    tasks: list[tuple[str, str]],
    use_subprocess: bool = False,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    model: str | None = None,
    profile: str | None = None,
    isolated: bool = False,
    output_schema: type | None = None,
    workdir: str | Path | None = None,
    context_turns: int | None = None,
    redact_secrets: bool = True,
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
    job = BatchJob(agent_ids=[t[0] for t in tasks], output_schema=output_schema)

    # Start all subagents (completions delivered via hooks)
    for agent_id, prompt in tasks:
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
            redact_secrets=redact_secrets,
        )

    logger.info(f"Started batch of {len(tasks)} subagents: {job.agent_ids}")
    return job


def subagent_parallel(
    tasks: list[tuple[str, str]],
    timeout: int = 300,
    use_subprocess: bool = False,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    model: str | None = None,
    profile: str | None = None,
    isolated: bool = False,
    output_schema: type | None = None,
    workdir: str | Path | None = None,
    context_turns: int | None = None,
    redact_secrets: bool = True,
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
        redact_secrets: If True (default), scrub common secret patterns from
            workspace context before passing it to subagents.

    Returns:
        List of result dicts in the same order as ``tasks``. Each dict has
        ``"status"`` (``"success"`` / ``"failure"`` / ``"timeout"``) and
        ``"result"`` (parsed object when ``output_schema`` is set, else the
        summary text from the subagent's ``complete`` block).

    Example::

        # Process three independent tasks in parallel
        results = subagent_parallel([
            ("researcher", "Research the top 5 Python async frameworks"),
            ("coder",      "Implement a basic async HTTP client"),
            ("tester",     "Write pytest tests for an async HTTP client"),
        ])
        for (agent_id, _), result in zip(tasks, results):
            print(f"{agent_id}: {result['status']} — {result['result'][:80]}")

        # With worktree isolation for concurrent file edits
        results = subagent_parallel(
            [("fix-a", "Fix bug in module A"), ("fix-b", "Fix bug in module B")],
            isolated=True,
        )

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
    """
    if not tasks:
        return []

    # Start all subagents; on failure, cancel any already-started ones to avoid orphans
    started_ids: list[str] = []
    try:
        for agent_id, prompt in tasks:
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

    logger.info(f"subagent_parallel: started {len(tasks)} subagents")

    # Collect results in parallel using BatchJob
    job = BatchJob(agent_ids=[t[0] for t in tasks])
    job.wait_all(timeout=timeout)

    # Return results in input order; fall back to failure for missing results.
    # When output_schema is set, parse the JSON result against the schema.
    raw_results = [
        asdict(
            job.results.get(
                agent_id, ReturnType("failure", "No result (timeout or missing)")
            )
        )
        for agent_id, _ in tasks
    ]
    if output_schema is not None:
        return [_parse_result(r, output_schema) for r in raw_results]
    return raw_results
