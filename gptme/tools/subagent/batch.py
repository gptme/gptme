"""Subagent batch execution — parallel task management.

Provides BatchJob for managing groups of subagents and subagent_batch()
for convenient fire-and-gather patterns.
"""

import logging
import threading
from dataclasses import dataclass, field

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
        """Wait for all subagents to complete.

        Args:
            timeout: Maximum seconds to wait for all subagents

        Returns:
            Dict mapping agent_id to status dict
        """
        import time
        from dataclasses import asdict

        start_time = time.time()
        for agent_id in self.agent_ids:
            remaining = max(1, timeout - int(time.time() - start_time))
            try:
                result = subagent_wait(agent_id, timeout=remaining)
                with self._lock:
                    if agent_id not in self.results:
                        self.results[agent_id] = ReturnType(
                            result.get("status", "failure"),
                            result.get("result"),
                        )
            except Exception as e:
                logger.warning(f"Error waiting for {agent_id}: {e}")
                with self._lock:
                    self.results[agent_id] = ReturnType("failure", str(e))

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
        )

    logger.info(f"Started batch of {len(tasks)} subagents: {job.agent_ids}")
    return job
