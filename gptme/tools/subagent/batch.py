"""Subagent batch execution — parallel task management.

Provides BatchJob for managing groups of subagents and subagent_batch()
for convenient fire-and-gather patterns. Also provides subagent_parallel()
for a simpler synchronous fan-out pattern.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import asdict, dataclass, field

from .api import subagent, subagent_wait
from .types import ReturnType

logger = logging.getLogger(__name__)


@dataclass
class BatchJob:
    """Manages a batch of subagents for parallel execution.

    Note: With the hook-based notification system, the orchestrator will receive
    completion messages automatically via the LOOP_CONTINUE hook. This class
    provides additional utilities for explicit synchronization when needed.
    """

    agent_ids: list[str]
    results: dict[str, ReturnType] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait_all(self, timeout: int = 300) -> dict[str, dict]:
        """Wait for all subagents to complete concurrently.

        Uses a thread pool to wait for all subagents simultaneously, so the
        wall-clock time is bounded by the slowest agent, not the sum of all
        agent times.

        Args:
            timeout: Maximum seconds to wait for all subagents

        Returns:
            Dict mapping agent_id to status dict
        """

        def _wait_one(agent_id: str, deadline: float) -> tuple[str, ReturnType]:
            import time

            remaining = max(1, int(deadline - time.monotonic()))
            try:
                result = subagent_wait(agent_id, timeout=remaining)
                return agent_id, ReturnType(
                    result.get("status", "failure"),
                    result.get("result"),
                )
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
                logger.warning(
                    f"wait_all() timed out after {timeout}s; marking remaining agents as timed out"
                )
                for agent_id in self.agent_ids:
                    with self._lock:
                        self.results.setdefault(
                            agent_id,
                            ReturnType(
                                "timeout", f"Agent did not complete within {timeout}s"
                            ),
                        )

        return {aid: asdict(r) for aid, r in self.results.items()}

    def is_complete(self) -> bool:
        """Check if all subagents have completed."""
        return len(self.results) == len(self.agent_ids)

    def get_completed(self) -> dict[str, dict]:
        """Get results of completed subagents so far."""
        from dataclasses import asdict

        with self._lock:
            return {aid: asdict(r) for aid, r in self.results.items()}


def subagent_batch(
    tasks: list[tuple[str, str]],
    use_subprocess: bool = False,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
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
    job = BatchJob(agent_ids=[t[0] for t in tasks])

    # Start all subagents (completions delivered via hooks)
    for agent_id, prompt in tasks:
        subagent(
            agent_id=agent_id,
            prompt=prompt,
            use_subprocess=use_subprocess,
            use_acp=use_acp,
            acp_command=acp_command,
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
            that exceed this deadline are reported with status ``"failure"``.
        use_subprocess: If True, run each subagent in a subprocess for output
            isolation. Subprocess mode captures stdout/stderr separately and
            supports hard-kill on timeout.
        use_acp: If True, run subagents via ACP protocol.
        acp_command: ACP agent command (default: "gptme-acp").
        model: Model override applied to every subagent. Pass ``None`` to
            inherit the parent's model.
        profile: Agent profile name applied to every subagent (e.g.
            ``"explorer"``, ``"developer"``, ``"verifier"``).
        isolated: If True, run each subagent in its own git worktree so file
            edits don't conflict between agents or with the parent.
        redact_secrets: If True (default), scrub common secret patterns from
            workspace context before passing it to subagents.

    Returns:
        List of result dicts in the same order as ``tasks``. Each dict has
        ``"status"`` (``"success"`` / ``"failure"`` / ``"timeout"``) and
        ``"result"`` (summary text from the subagent's ``complete`` block).

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
    """
    if not tasks:
        return []

    # Start all subagents
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
            redact_secrets=redact_secrets,
        )

    logger.info(f"subagent_parallel: started {len(tasks)} subagents")

    # Collect results in parallel using BatchJob
    job = BatchJob(agent_ids=[t[0] for t in tasks])
    job.wait_all(timeout=timeout)

    # Return results in input order; fall back to failure for missing results
    return [
        asdict(
            job.results.get(
                agent_id, ReturnType("failure", "No result (timeout or missing)")
            )
        )
        for agent_id, _ in tasks
    ]
