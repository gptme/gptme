"""Subagent public API — create, monitor, and manage subagents.

Contains the main subagent() function and supporting status/wait/read_log
functions that form the public interface of the subagent tool.
"""

import logging
import random
import string
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from ...llm.retry_abort import bind_thread_generation, release_thread
from . import execution as _exec
from .concurrency import get_slot_sem
from .control import append_control_op
from .hooks import notify_completion
from .types import (
    ReturnType,
    Role,
    Subagent,
    SubtaskDef,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
    clarification_result_from_content,
    resolve_role_defaults,
    set_subagent_result_if_absent,
    update_subagent_result_with_branch,
)

logger = logging.getLogger(__name__)


def _write_cancel_op(logdir: Path, agent_id: str) -> None:
    """Append a cancel op to logdir/control.jsonl for the cooperative checkpoint.

    Uses the same JSONL/flock conventions as prompt_queue.py.
    The subagent's STEP_PRE checkpoint hook reads this file each step and exits
    cleanly when it sees a cancel op, releasing its concurrency slot.
    """
    import importlib
    import json
    from datetime import datetime, timezone

    try:
        fcntl = importlib.import_module("fcntl")
    except ImportError:  # Windows / environments without fcntl
        fcntl = None

    control_file = logdir / "control.jsonl"
    record = json.dumps(
        {
            "op": "cancel",
            "agent_id": agent_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

    lock_file = logdir / ".control.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.touch(exist_ok=True)

    with lock_file.open("r+") as lock_fd:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with control_file.open("a") as f:
                f.write(record + "\n")
        finally:
            if fcntl is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _wait_for_cached_subagent_result(
    agent_id: str, timeout: float = 0.05, poll_interval: float = 0.005
) -> ReturnType | None:
    """Briefly wait for a concurrent watchdog/cancel result to hit the cache."""
    deadline = time.monotonic() + timeout
    while True:
        with _subagent_results_lock:
            cached_result = _subagent_results.get(agent_id)
        if cached_result is not None:
            return cached_result

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval, remaining))


def subagent(
    agent_id: str,
    prompt: str,
    mode: Literal["executor", "planner"] = "executor",
    subtasks: list[SubtaskDef] | None = None,
    execution_mode: Literal["parallel", "sequential"] = "parallel",
    context_mode: Literal["full", "selective"] = "full",
    context_include: list[str] | None = None,
    output_schema: "type | dict | None" = None,
    use_subprocess: bool | None = None,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    profile: str | None = None,
    model: str | None = None,
    isolated: bool | None = None,
    isolation: Literal["worktree"] | None = None,
    timeout: int = 1800,
    role: Role | None = None,
    redact_secrets: bool = True,
    context_window: int | None = None,
    max_time: float | None = None,
    context_turns: int | None = None,
    workdir: str | Path | None = None,
):
    """Starts an asynchronous subagent. Returns None immediately.

    Subagent completions are delivered via the LOOP_CONTINUE hook, enabling
    a "fire-and-forget-then-get-alerted" pattern where the orchestrator can
    continue working and get notified when subagents finish.

    Profile auto-detection: If ``agent_id`` matches a known profile name
    (e.g. "explorer", "researcher", "developer", "verifier") or a common role alias
    ("explore"→"explorer", "research"→"researcher", "impl"/"dev"→"developer", "verify"→"verifier"),
    the profile is applied automatically — no need to pass ``profile`` separately.

    Role-based defaults (``role`` parameter):

    - ``"explore"``: Defaults profile to ``explorer`` (read-only analysis)
    - ``"implement"``: Defaults profile to ``developer`` (full capability)
    - ``"verify"``: Defaults profile to ``verifier`` plus ``use_subprocess=True`` and ``isolated=True`` (read-only validation in isolation)

    Explicit arguments override role defaults.

    Args:
        agent_id: Unique identifier for the subagent. If it matches a known
            profile name (or a common alias like ``impl``/``dev``), that
            profile is auto-applied (unless ``profile`` is explicitly set
            to something else).
        prompt: Task prompt for the subagent (used as context for planner mode)
        mode: "executor" for single task, "planner" for delegating to multiple executors
        subtasks: List of subtask definitions for planner mode (required when mode="planner")
        execution_mode: "parallel" (default) runs all subtasks concurrently,
                       "sequential" runs subtasks one after another.
                       Only applies to planner mode.
        context_mode: Controls what context is shared with the subagent:
            - "full" (default): Share complete context (agent identity, tools, workspace)
            - "selective": Share only specified context components (requires context_include)
        context_include: For selective mode, list of context components to include:
            - Thread mode supports "agent" and "tools"
            - Subprocess mode also supports "workspace", which maps to the CLI's "files" context
            Legacy subprocess values like "files", "cmd", and "all" are still accepted.
        use_subprocess: If True, run subagent in subprocess for output isolation.
            Subprocess mode captures stdout/stderr separately from the parent.
        use_acp: If True, run subagent via ACP (Agent Client Protocol).
            This enables multi-harness support — the subagent can be any
            ACP-compatible agent (gptme, Claude Code, Cursor, etc.).
            Requires the ``acp`` package: pip install 'gptme[acp]'.
        acp_command: ACP agent command to invoke (default: "gptme-acp").
            Only used when use_acp=True. Can be any ACP-compatible CLI.
        profile: Agent profile name to apply. Profiles provide:
            - System prompt customization (behavioral hints)
            - Tool access restrictions (which tools the subagent can use)
            - Behavior rules (read-only, no-network, etc.)
            Use 'gptme-util profile list' to see available profiles.
            Built-in profiles: default, explorer, researcher, developer, verifier, isolated, computer-use, browser-use.
            If not set, auto-detected from agent_id when it matches a profile name.
        model: Model to use for the subagent. Overrides parent's model.
            Useful for routing cheap tasks to faster/cheaper models.
        isolated: If True, run the subagent in a git worktree for filesystem
            isolation. The subagent gets its own copy of the repository and
            can modify files without affecting the parent. The worktree is
            automatically cleaned up after the subagent completes.
            Falls back to a temporary directory if not in a git repo.
            Prefer ``isolation="worktree"`` for new code — it is the string-based
            API equivalent and enables smarter cleanup behaviour.
        isolation: String-based isolation mode. Use ``"worktree"`` to create a
            temporary git worktree for the subagent, giving it an isolated copy
            of the repository to work in. On completion:

            - **No local changes**: worktree directory *and* branch are removed
              automatically (zero cleanup needed).
            - **Local changes exist**: the branch is preserved and its name is
              reported in the result so the orchestrator can inspect or merge it.
              The working-tree directory is still removed.

            Falls back to a temporary directory when not in a git repository.
            Equivalent to ``isolated=True`` but adds smart cleanup behaviour.
        timeout: Maximum seconds before the subprocess monitor kills the
            subagent (default 1800 = 30 min). Only applies to subprocess mode.
        redact_secrets: If True (default), scrub common secret patterns from
            workspace context messages before they are passed to the subagent.
            Redacts values from lines where the variable name matches patterns
            like API_KEY, TOKEN, PASSWORD, PRIVATE_KEY, etc.

            Note: subagents do NOT inherit the parent's conversation history —
            they always start with a fresh context containing only the task
            prompt and workspace context (files from gptme.toml [prompt] files,
            and context_cmd output when context_mode="full"). This option
            sanitizes that inherited workspace context.

            Only applies to thread-mode subagents (subprocess and ACP modes
            run as a separate gptme process and handle their own context).
            Set to False to disable redaction if legitimate config values are
            being incorrectly redacted.
        context_window: Limit workspace context messages passed to the subagent.
            Controls how much of the workspace context (files from gptme.toml
            [prompt] files, context_cmd output) is shared with the subagent.

            - ``None`` (default): no limit — full workspace context is shared.
            - ``0``: minimal context — only agent identity and tools; no workspace
              files or context_cmd output. Equivalent to
              ``context_mode="selective", context_include=["agent", "tools"]``.
            - ``N > 0``: at most N workspace context messages are passed.

            Use ``context_window=0`` when the subagent does not need the parent
            workspace configuration (e.g. a verification task that should only
            see what the orchestrator explicitly tells it).

            Only applies to thread-mode subagents; has no effect in subprocess
            or ACP modes (which build their own context as a separate process).
        max_time: Wall-clock time limit in seconds. When set, a watchdog timer
            marks the subagent result as ``"timeout"`` after ``max_time`` seconds
            and delivers a timeout status notification via the LOOP_CONTINUE hook.
            In subprocess mode the child process is terminated. In thread mode
            the background thread is not force-stopped; callers see the cached
            timeout result immediately while the thread continues until it
            finishes naturally. Defaults to ``None`` (no limit).

            Use this for defensive orchestration (prevent a stuck subagent from
            blocking the parent) or hard time budgets in autonomous sessions.
            ``max_time=None`` is fully backwards-compatible — no change in behavior.
        context_turns: Number of recent parent conversation turns to forward to
            the subagent as context. A "turn" starts at a user message and
            includes all subsequent assistant and tool-result (system) messages
            until the next user message, so the total message count per turn
            varies with the number of tool calls. The messages are injected as
            a system message so the subagent understands what the parent has
            been doing without confusing its own conversation flow.

            - ``None`` (default): no parent context forwarded (current behavior).
            - ``N > 0``: forward the last N turns from the parent's active log.

            The parent log is fetched automatically from the currently active
            ``LogManager`` (set by the chat loop via ``ContextVar``). This works
            when ``subagent()`` is called from within the ``ipython`` tool during
            a running chat session.

            Use this when the subagent needs awareness of what the parent has
            already done (e.g. "the parent tried A and B, now try C") or when
            the task prompt alone doesn't provide enough context.

            Only applies to thread-mode subagents; has no effect in subprocess
            or ACP modes.
        workdir: Working directory for the subagent. Defaults to the current
            working directory (``Path.cwd()``) when ``None``.

            Use this when you want the subagent to operate in a specific
            directory — for example, when a ``cd`` into a project with a
            ``gptme.toml`` triggers workspace detection and you want the
            subagent to load that workspace's config:

            .. code-block:: python

                subagent("impl", "Add feature X", workdir="/path/to/project",
                         use_subprocess=True)

            In subprocess mode the subagent process starts with this as its
            ``cwd``, so it picks up the ``gptme.toml`` from that directory.
            In thread mode the workspace context (files, ``context_cmd``) is
            loaded relative to this path.

    Returns:
        None: Starts asynchronous execution.
            In executor mode, starts a single task execution.
            In planner mode, starts execution of all subtasks using the specified execution_mode.

            Executors use the `complete` tool to signal completion with a summary.
            The full conversation log is available at the logdir path.
    """
    if context_window is not None and context_window < 0:
        raise ValueError(
            f"context_window must be None, 0, or a positive integer, got {context_window!r}"
        )
    if context_turns is not None and context_turns <= 0:
        raise ValueError(
            f"context_turns must be None or a positive integer, got {context_turns!r}"
        )
    if isolation is not None and isolation != "worktree":
        raise ValueError(
            f"Unknown isolation mode: {isolation!r}. Supported values: 'worktree'."
        )

    # isolation="worktree" is the string-based API equivalent of isolated=True.
    # When isolation is set, it overrides isolated (but both can still coexist).
    if isolation == "worktree":
        isolated = True

    # Capture the parent session's logdir unconditionally — used by SESSION_END
    # cleanup to scope cancellation to this conversation only (multi-session safety).
    # LogManager.get_current_log() reads a ContextVar set by the chat loop, so
    # this works when subagent() is called from within an ipython tool execution.
    from ...logmanager import LogManager  # fmt: skip

    parent_log = LogManager.get_current_log()
    parent_logdir = (
        getattr(parent_log, "logdir", None) if parent_log is not None else None
    )

    parent_messages = None
    if context_turns is not None:
        if parent_log is not None:
            msgs = parent_log.log
            # Slice from the N-th-from-last user message so tool-result system
            # messages within a turn are included and the count is exact.
            # Fallback to user_indices[0] (not 0) so leading system bootstrap
            # messages (identity, workspace context) are never forwarded when
            # context_turns exceeds available turns.
            user_indices = [i for i, m in enumerate(msgs) if m.role == "user"]
            if user_indices:
                start = (
                    user_indices[-context_turns]
                    if len(user_indices) >= context_turns
                    else user_indices[0]
                )
                parent_messages = list(msgs[start:])
            else:
                parent_messages = list(msgs)
        else:
            logger.warning(
                "context_turns=%d set but no active LogManager found; "
                "parent context will not be forwarded",
                context_turns,
            )

    # noreorder
    from gptme.cli.main import get_logdir  # fmt: skip
    from gptme.llm.models import get_default_model  # fmt: skip

    from ...profiles import get_profile as _get_profile  # fmt: skip

    # Track whether profile was set explicitly by the caller (before any auto-detection).
    # This lets role= override agent_id auto-detection without overriding explicit profile=.
    explicit_profile = profile is not None

    # Auto-detect profile from agent_id when no explicit profile is set
    if profile is None:
        if _get_profile(agent_id) is not None:
            profile = agent_id
            logger.info(f"Auto-detected profile '{profile}' from agent_id")
        else:
            # Common role aliases to reduce agent_id/profile duplication.
            # Example: subagent("impl", "...") maps to profile="developer".
            profile_aliases = {
                "explore": "explorer",
                "research": "researcher",
                "impl": "developer",
                "dev": "developer",
                "verify": "verifier",
                "check": "verifier",
            }
            aliased_profile = profile_aliases.get(agent_id)
            if aliased_profile and _get_profile(aliased_profile) is not None:
                profile = aliased_profile
                logger.info(
                    f"Auto-detected profile '{profile}' from agent_id alias '{agent_id}'"
                )

    # Role-based defaults: explicit caller args > role defaults > agent_id auto-detection
    if role is not None:
        use_sub, use_iso, role_profile = resolve_role_defaults(
            role,
            use_subprocess,  # None = not set; True/False = explicit override
            isolated,
        )
        # Role-derived profile overrides agent_id auto-detection but NOT an explicit profile=
        if not explicit_profile and role_profile is not None:
            profile = role_profile
            logger.info(f"Set profile '{profile}' from role='{role}'")
        use_subprocess = use_sub
        isolated = use_iso
        logger.info(
            f"Role '{role}' resolved: profile={profile}, use_subprocess={use_subprocess}, isolated={isolated}"
        )

    # Normalize to bool after role resolution (None = "not set" → False default)
    use_subprocess = bool(use_subprocess)
    isolated = bool(isolated)

    # Determine model: explicit parameter > parent's model
    model_name: str | None
    if model:
        model_name = model
    else:
        current_model = get_default_model()
        model_name = current_model.full if current_model else None

    # Resolve explicit workdir once, shared by the planner and executor paths.
    # When workdir is None each path falls back to Path.cwd() at spawn time.
    workdir_path: Path | None = None
    if workdir is not None:
        workdir_path = Path(workdir).resolve()
        if not workdir_path.exists():
            raise ValueError(f"workdir does not exist: {workdir_path}")
        if not workdir_path.is_dir():
            raise ValueError(f"workdir is not a directory: {workdir_path}")

    # Clear any stale cached result for this agent_id before starting a new run.
    # Without this, a reused deterministic id (e.g. "<item>-s0" in a pipeline) can
    # return the previous run's terminal result from the shared cache, hiding the
    # current run entirely.
    with _subagent_results_lock:
        _subagent_results.pop(agent_id, None)

    if mode == "planner":
        if context_turns is not None:
            logger.warning(
                "context_turns=%d set but planner mode does not forward parent context; "
                "parameter is ignored",
                context_turns,
            )
        if not subtasks:
            raise ValueError("Planner mode requires subtasks parameter")

        # Register the planner as a subagent and launch the watchdog timer.
        # The planner runs synchronously in the parent thread, so the timer
        # logs and marks the result as "timeout" on expiry — same pattern as
        # thread-mode watchdog.
        logdir = get_logdir(f"subagent-{agent_id}")
        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=None,
            logdir=logdir,
            model=model_name,
            context_mode=context_mode,
            context_include=context_include,
            profile=profile,
            isolated=isolated,
            isolation_mode=isolation,
            redact_secrets=redact_secrets,
            context_window=context_window,
            max_time=max_time,
            parent_logdir=parent_logdir,
        )
        with _subagents_lock:
            _subagents.append(sa)

        _timer = None
        if max_time is not None:
            _timer = threading.Timer(
                max_time, _timeout_subagent, args=(agent_id, max_time)
            )
            _timer.daemon = True
            _timer.start()

        try:
            return _exec._run_planner(
                agent_id,
                prompt,
                subtasks,
                execution_mode,
                context_mode,
                context_include,
                model_name,
                profile_name=profile,
                redact_secrets=redact_secrets,
                context_window=context_window,
                workdir=workdir_path,
                parent_logdir=parent_logdir,
            )
        finally:
            if _timer is not None:
                _timer.cancel()

    # Validate context_mode parameters
    if context_mode == "selective" and not context_include:
        raise ValueError(
            "context_include parameter required when context_mode='selective'"
        )

    def random_string(n):
        s = string.ascii_lowercase + string.digits
        return "".join(random.choice(s) for _ in range(n))

    name = f"subagent-{agent_id}"
    logdir = get_logdir(name + "-" + random_string(4))

    # Resolve workspace: explicit workdir (validated above) > current working dir
    if workdir_path is not None:
        workspace = workdir_path
    else:
        # Get workspace, handling case where cwd was deleted (e.g., in tests)
        try:
            workspace = Path.cwd()
        except FileNotFoundError:
            # Fallback to logdir's parent if cwd doesn't exist
            workspace = logdir.parent

    # Set up worktree isolation if requested
    worktree_path: Path | None = None
    repo_path: Path | None = None
    if isolated:
        from ...util.git_worktree import create_worktree, get_git_root

        repo_path = get_git_root(workspace)
        if repo_path:
            try:
                worktree_path = create_worktree(
                    repo_path,
                    branch_name=f"subagent-{agent_id}-{uuid.uuid4().hex[:8]}",
                )
                workspace = worktree_path
                logger.info(
                    f"Subagent {agent_id} isolated in worktree: {worktree_path}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to create worktree for {agent_id}, "
                    f"falling back to temp dir: {e}"
                )
                import tempfile

                worktree_path = Path(tempfile.mkdtemp(prefix=f"subagent-{agent_id}-"))
                workspace = worktree_path
        else:
            import tempfile

            worktree_path = Path(tempfile.mkdtemp(prefix=f"subagent-{agent_id}-"))
            workspace = worktree_path
            logger.info(
                f"Not in a git repo, using temp dir for {agent_id}: {worktree_path}"
            )

    if redact_secrets and (use_acp or use_subprocess):
        exec_mode = "ACP" if use_acp else "subprocess"
        logger.debug(
            f"Subagent {agent_id}: 'redact_secrets=True' has no effect in {exec_mode} mode "
            "(only thread-mode subagents inherit workspace context from the parent process)"
        )

    if use_acp:
        # ACP mode: multi-harness support via Agent Client Protocol
        if use_subprocess:
            logger.warning(
                f"Subagent {agent_id}: both 'use_acp' and 'use_subprocess' are set; "
                "'use_subprocess' is ignored (ACP mode takes precedence)"
            )
        logger.info(f"Starting subagent {agent_id} in ACP mode (command={acp_command})")
        if profile:
            logger.info(f"  with profile: {profile}")
        # Warn about parameters not forwarded to ACP agent
        if model:
            logger.warning(
                f"Subagent {agent_id}: 'model' is not forwarded to ACP agent (ignored)"
            )
        if output_schema is not None:
            logger.warning(
                f"Subagent {agent_id}: 'output_schema' is not supported in ACP mode (ignored)"
            )
        if context_mode != "full":
            logger.warning(
                f"Subagent {agent_id}: 'context_mode={context_mode!r}' is not supported in ACP mode (ignored)"
            )
        if context_include:
            logger.warning(
                f"Subagent {agent_id}: 'context_include' is not supported in ACP mode (ignored)"
            )
        if context_turns is not None:
            logger.warning(
                "context_turns=%d set but ACP mode does not forward parent context; "
                "parameter is ignored",
                context_turns,
            )

        def run_acp_subagent():
            # Bind retry generation at thread birth so test-teardown interrupts
            # abort backoffs even for LLM calls that start after teardown.
            bind_thread_generation()
            _sem = get_slot_sem()
            _sem.acquire()
            try:
                with _subagent_results_lock:
                    if agent_id in _subagent_results:
                        logger.info(
                            f"Skipping cancelled queued ACP subagent {agent_id}"
                        )
                        with _subagents_lock:
                            sa = next(
                                (s for s in _subagents if s.agent_id == agent_id), None
                            )
                        if sa:
                            _exec._cleanup_isolation(sa)
                        return

                import asyncio

                async def _acp_run():
                    from ...acp.client import GptmeAcpClient

                    collected_text: list[str] = []

                    def on_update(session_id: str, update) -> None:
                        """Collect text from session updates."""
                        # Extract text from agent_message_chunk updates
                        update_type = getattr(update, "type", None)
                        if update_type == "agent_message_chunk":
                            chunk = getattr(update, "chunk", None)
                            if chunk:
                                text = getattr(chunk, "text", None) or (
                                    chunk.get("text")
                                    if isinstance(chunk, dict)
                                    else None
                                )
                                if text:
                                    collected_text.append(text)

                    async with GptmeAcpClient(
                        workspace=workspace,
                        command=acp_command,
                        auto_confirm=True,
                        on_update=on_update,
                    ) as client:
                        result = await client.run(prompt, cwd=workspace)
                        stop_reason = getattr(result, "stop_reason", "unknown")
                        result_text = (
                            "".join(collected_text) if collected_text else None
                        )

                        clarification_result = (
                            clarification_result_from_content(result_text)
                            if result_text
                            else None
                        )
                        if clarification_result:
                            status = clarification_result.status
                            summary = clarification_result.result
                        else:
                            status = (
                                "success" if stop_reason == "end_turn" else "failure"
                            )
                            summary = (
                                result_text[:500]
                                if result_text
                                else f"ACP stop_reason={stop_reason}"
                            )
                        return status, summary

                try:
                    status, summary = asyncio.run(_acp_run())

                    with _subagents_lock:
                        sa_ref = next(
                            (s for s in _subagents if s.agent_id == agent_id), None
                        )
                    in_tok, out_tok = (
                        sa_ref._read_token_stats()
                        if sa_ref is not None
                        else (None, None)
                    )
                    result = ReturnType(
                        status,
                        summary,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                    )
                    if not set_subagent_result_if_absent(agent_id, result):
                        return
                    notify_completion(
                        agent_id,
                        status,
                        _exec._summarize_result(result, max_chars=200),
                    )
                except Exception as e:
                    logger.error(f"ACP subagent {agent_id} failed: {e}", exc_info=True)
                    if not set_subagent_result_if_absent(
                        agent_id, ReturnType("failure", str(e))
                    ):
                        return
                    notify_completion(agent_id, "failure", f"ACP error: {e}")
                finally:
                    with _subagents_lock:
                        sa_ref = next(
                            (s for s in _subagents if s.agent_id == agent_id), None
                        )
                    if sa_ref:
                        # ACP always caches a result above before reaching this
                        # cleanup, so patch the already-stored result with any
                        # preserved branch (mirrors the thread-mode fallback).
                        preserved_branch = _exec._cleanup_isolation(sa_ref)
                        if preserved_branch:
                            update_subagent_result_with_branch(
                                agent_id,
                                preserved_branch,
                                has_output_schema=bool(sa_ref.output_schema),
                            )
            finally:
                release_thread()
                _sem.release()

        t = threading.Thread(target=run_acp_subagent, daemon=True)

        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=t,
            logdir=logdir,
            model=model_name,
            context_mode=context_mode,
            context_include=context_include,
            profile=profile,
            output_schema=output_schema,
            use_acp=True,
            process=None,
            execution_mode="acp",
            acp_command=acp_command,
            workdir=workdir_path,
            isolated=isolated,
            isolation_mode=isolation,
            worktree_path=worktree_path,
            repo_path=repo_path,
            role=role,
            max_time=max_time,
            context_turns=context_turns,
            parent_logdir=parent_logdir,
        )
        # Append sa before starting the thread so the finally block can find it
        # (avoids race condition where fast completion can't locate sa in _subagents)
        with _subagents_lock:
            _subagents.append(sa)
        t.start()

    elif use_subprocess:
        # Subprocess mode: better output isolation, gated by the concurrency semaphore.
        # A launcher thread acquires the slot before starting the OS process so that
        # excess agents queue (rather than all starting at once).
        logger.info(f"Starting subagent {agent_id} in subprocess mode")
        if context_turns is not None:
            logger.warning(
                "context_turns=%d set but subprocess mode does not forward parent context; "
                "parameter is ignored",
                context_turns,
            )
        if profile:
            logger.info(f"  with profile: {profile}")
        # Convert output_schema for the subprocess launcher.
        # The CLI --output-schema flag only accepts "module:ClassName" format,
        # so all schemas (Pydantic, plain-dict, annotated) are passed via
        # output_schema_dict and injected into the prompt instead.
        output_schema_str = None
        output_schema_dict = None
        if output_schema is not None:
            if hasattr(output_schema, "model_json_schema"):
                # Pydantic model: extract JSON Schema and inject via prompt,
                # not via --output-schema (which expects module:ClassName).
                output_schema_dict = output_schema.model_json_schema()
            elif isinstance(output_schema, dict):
                from .hooks import _dict_to_jsonschema

                output_schema_dict = _dict_to_jsonschema(output_schema)
            elif hasattr(output_schema, "__annotations__"):
                output_schema_dict = {"type": "object"}

        def _launch_subprocess():
            _sem = get_slot_sem()
            _sem.acquire()
            try:
                with _subagent_results_lock:
                    if agent_id in _subagent_results:
                        logger.info(
                            f"Skipping cancelled queued subprocess subagent {agent_id}"
                        )
                        _exec._cleanup_isolation(sa)
                        return
                process = _exec._run_subagent_subprocess(
                    prompt=prompt,
                    logdir=logdir,
                    model=model_name,
                    workspace=workspace,
                    context_mode=context_mode,
                    context_include=context_include,
                    output_schema=output_schema_str,
                    output_schema_dict=output_schema_dict,
                    profile=profile,
                )
                # Subagent is a frozen dataclass; install the live process on the
                # pre-registered object so queued agents become inspectable once
                # they leave the semaphore.
                object.__setattr__(sa, "process", process)
                # Monitor blocks until the process finishes (slot stays acquired)
                _exec._monitor_subprocess(sa)
            except Exception as e:
                logger.error(
                    f"Subagent {agent_id} subprocess failed: {e}", exc_info=True
                )
                if set_subagent_result_if_absent(
                    agent_id, ReturnType("failure", str(e))
                ):
                    notify_completion(agent_id, "failure", f"Subprocess failed: {e}")
                _exec._cleanup_isolation(sa)
            finally:
                # Mark the prompt queue as closed: the subprocess has exited (or
                # never started). Any steer attempt after this point would write
                # to a queue that no process will drain. Set before _sem.release()
                # so subagent_steer() can detect this via prompt_queue_closed
                # even during the narrow window between process exit and result
                # caching.
                sa.prompt_queue_closed.set()
                _sem.release()

        launcher = threading.Thread(target=_launch_subprocess, daemon=True)

        # Pre-register with launcher thread so status/wait/cancel work while queued.
        # process=None here; is_running() falls through to thread.is_alive().
        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=launcher,
            logdir=logdir,
            model=model_name,
            context_mode=context_mode,
            context_include=context_include,
            profile=profile,
            output_schema=output_schema,
            process=None,
            execution_mode="subprocess",
            workdir=workdir_path,
            isolated=isolated,
            isolation_mode=isolation,
            worktree_path=worktree_path,
            repo_path=repo_path,
            timeout=timeout,
            role=role,
            max_time=max_time,
            context_turns=context_turns,
            parent_logdir=parent_logdir,
        )
        with _subagents_lock:
            _subagents.append(sa)
        launcher.start()
    else:
        # Thread mode: original behavior, gated by the concurrency semaphore.
        # The semaphore is acquired before starting LLM work and released in
        # finally so excess agents queue until a slot opens.
        #
        # Pre-create the event so run_subagent captures it directly (no lock+lookup
        # window between _create_subagent_thread returning and finding sa to set it).
        _pqc = threading.Event()

        def run_subagent():
            # Bind retry generation at thread birth so test-teardown interrupts
            # abort backoffs even for LLM calls that start after teardown.
            bind_thread_generation()
            _sem = get_slot_sem()
            _sem.acquire()
            try:
                with _subagent_results_lock:
                    if agent_id in _subagent_results:
                        logger.info(
                            f"Skipping cancelled queued thread subagent {agent_id}"
                        )
                        with _subagents_lock:
                            sa = next(
                                (s for s in _subagents if s.agent_id == agent_id), None
                            )
                        if sa:
                            _exec._cleanup_isolation(sa)
                        return
                try:
                    _exec._create_subagent_thread(
                        prompt=prompt,
                        logdir=logdir,
                        model=model_name,
                        context_mode=context_mode,
                        context_include=context_include,
                        workspace=workspace,
                        target="parent",
                        output_schema=output_schema,
                        profile_name=profile,
                        agent_id=agent_id,
                        redact_secrets=redact_secrets,
                        context_window=context_window,
                        parent_messages=parent_messages,
                        prompt_queue_closed=_pqc,
                    )
                except Exception as e:
                    # If subagent creation fails, notify with error status
                    logger.error(f"Subagent {agent_id} failed during execution: {e}")
                    if not set_subagent_result_if_absent(
                        agent_id, ReturnType("failure", str(e))
                    ):
                        with _subagents_lock:
                            sa = next(
                                (s for s in _subagents if s.agent_id == agent_id), None
                            )
                        if sa:
                            _exec._cleanup_isolation(sa)
                        return
                    try:
                        notify_completion(agent_id, "failure", f"Execution failed: {e}")
                    except Exception as notify_err:
                        logger.warning(f"Failed to notify subagent error: {notify_err}")
                    # Clean up worktree isolation even on failure
                    with _subagents_lock:
                        sa = next(
                            (s for s in _subagents if s.agent_id == agent_id), None
                        )
                    if sa:
                        _exec._cleanup_isolation(sa)
                    return

                # Notify via hook system when complete (only if successful)
                with _subagents_lock:
                    sa = next((s for s in _subagents if s.agent_id == agent_id), None)
                if sa:
                    # prompt_queue_closed is already set inside _create_subagent_thread
                    # (right after chat() returns, before this thread gets the lock).
                    # Use _read_log() instead of status(): the thread is still alive here,
                    # so status() would return "running" and poison the result cache.
                    result = sa._read_log()
                    # Clean up isolation first so preserved branch name can be
                    # included in the result returned to callers.
                    preserved_branch = _exec._cleanup_isolation(sa)
                    # Skip appending human-readable branch text when output_schema
                    # is set — the result string is JSON that callers parse, and
                    # appending text after it would break that parse.
                    if (
                        preserved_branch
                        and isinstance(result.result, str)
                        and not sa.output_schema
                    ):
                        from .types import ReturnType as _ReturnType

                        result = _ReturnType(
                            result.status,
                            f"{result.result}\n\nChanges preserved on branch "
                            f"{preserved_branch!r}"
                            f" — merge with: git merge {preserved_branch}",
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                        )
                    if not set_subagent_result_if_absent(agent_id, result):
                        # Timeout/cancel won the cache race. Cleanup already ran above.
                        # Patch the stored result with branch info so callers can find it.
                        if preserved_branch:
                            update_subagent_result_with_branch(
                                agent_id,
                                preserved_branch,
                                has_output_schema=bool(sa.output_schema),
                            )
                        return
                    try:
                        summary = _exec._summarize_result(result, max_chars=200)
                        notify_completion(agent_id, result.status, summary)
                    except Exception as e:
                        logger.warning(f"Failed to notify subagent completion: {e}")
            finally:
                release_thread()
                _sem.release()

        # Create thread (don't start yet)
        t = threading.Thread(
            target=run_subagent,
            daemon=True,
        )

        # Register Subagent BEFORE starting thread to avoid race condition:
        # run_subagent closure looks up agent_id in _subagents, which would
        # return None if the thread runs before _subagents.append(sa).
        # Pass the pre-created _pqc event so sa.prompt_queue_closed and the event
        # captured by run_subagent are the same object.
        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=t,
            logdir=logdir,
            model=model_name,
            context_mode=context_mode,
            context_include=context_include,
            profile=profile,
            output_schema=output_schema,
            process=None,
            execution_mode="thread",
            workdir=workdir_path,
            isolated=isolated,
            isolation_mode=isolation,
            worktree_path=worktree_path,
            repo_path=repo_path,
            role=role,
            redact_secrets=redact_secrets,
            context_window=context_window,
            max_time=max_time,
            context_turns=context_turns,
            parent_logdir=parent_logdir,
            prompt_queue_closed=_pqc,
        )
        with _subagents_lock:
            _subagents.append(sa)
        t.start()

    # Launch max_time watchdog after all execution paths have registered the subagent.
    # The watchdog fires _timeout_subagent() after max_time seconds, which marks
    # a timeout result and delivers a notification via the LOOP_CONTINUE hook.
    if max_time is not None:
        _timer = threading.Timer(max_time, _timeout_subagent, args=(agent_id, max_time))
        _timer.daemon = True
        _timer.start()


def _timeout_subagent(agent_id: str, max_time: float) -> None:
    """Internal: auto-cancel a subagent that exceeded its max_time wall-clock budget.

    Called by the watchdog timer launched in subagent(). Uses set_subagent_result_if_absent
    so a subagent that already completed normally is not affected (the race is handled
    atomically).
    """
    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)

    if sa is None or not sa.is_running():
        return  # Already finished normally before the timer fired

    timeout_result = ReturnType(
        "timeout", f"Auto-cancelled after {max_time}s (max_time exceeded)"
    )
    if not set_subagent_result_if_absent(agent_id, timeout_result):
        return  # Another result was set concurrently (subagent finished at the same time)

    if sa.execution_mode == "subprocess" and sa.process:
        sa.process.terminate()
        try:
            sa.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sa.process.kill()
            sa.process.wait()
        logger.info(
            f"Subagent '{agent_id}' subprocess killed after {max_time}s (max_time)."
        )
    else:
        logger.info(
            f"Subagent '{agent_id}' marked timed-out after {max_time}s "
            "(thread will stop at its next checkpoint)."
        )

    notify_completion(agent_id, "timeout", f"Timed out after {max_time}s")


def subagent_cancel(agent_id: str) -> str:
    """Cancel a running subagent.

    For subprocess-mode subagents, writes a cancel op to ``logdir/control.jsonl``
    so the agent's cooperative checkpoint can exit cleanly before SIGTERM arrives,
    then sends SIGTERM (and SIGKILL after 5s) as escalation.

    For thread-mode subagents, writes the cancel op and marks the result cache so
    callers don't block.  The thread stops at its next STEP_PRE checkpoint and
    releases its concurrency slot.

    ACP-mode subagents keep today's cache-mark-only behavior (no control file).

    Args:
        agent_id: The subagent to cancel

    Returns:
        A human-readable status message
    """
    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)

    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")

    if not sa.is_running():
        return f"Subagent '{agent_id}' is not running (already finished)."

    cancelled_result = ReturnType("cancelled", "Cancelled by orchestrator")

    if sa.execution_mode == "subprocess" and sa.process:
        # Mark result BEFORE writing the control file so the cooperative checkpoint
        # can never observe the cancel op without a "cancelled" result already in
        # the cache — preventing a race where the hook's set_subagent_result_if_absent
        # would win with the wrong status.
        if not set_subagent_result_if_absent(agent_id, cancelled_result):
            return f"Subagent '{agent_id}' already finished before cancellation."
        try:
            append_control_op(sa.logdir, "cancel", agent_id=agent_id)
        except OSError as e:
            logger.warning(
                "Failed to write cancel control op for '%s': %s", agent_id, e
            )
        sa.process.terminate()
        try:
            sa.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sa.process.kill()
            sa.process.wait()
        logger.info(f"Subagent '{agent_id}' subprocess terminated.")
        return f"Subagent '{agent_id}' cancelled."
    if sa.execution_mode == "thread":
        # Thread mode: mark result BEFORE writing the control file (same race fix).
        # The thread stops at its next STEP_PRE checkpoint and releases the slot.
        if not set_subagent_result_if_absent(agent_id, cancelled_result):
            return f"Subagent '{agent_id}' already finished before cancellation."
        try:
            append_control_op(sa.logdir, "cancel", agent_id=agent_id)
        except OSError as e:
            logger.warning(
                "Failed to write cancel control op for '%s': %s — "
                "falling back to in-memory cancel_event",
                agent_id,
                e,
            )
            # Control file is unavailable; signal the thread in-memory so the
            # STEP_PRE checkpoint hook can still stop it at its next step.
            sa.cancel_event.set()
        logger.info(
            f"Subagent '{agent_id}' marked cancelled (thread will stop at next checkpoint)."
        )
        return (
            f"Subagent '{agent_id}' marked as cancelled. "
            "The background thread will stop at its next cooperative checkpoint."
        )
    # ACP mode: threads cannot be stopped; keep cache-mark-only behavior.
    if not set_subagent_result_if_absent(agent_id, cancelled_result):
        return f"Subagent '{agent_id}' already finished before cancellation."
    logger.info(
        f"Subagent '{agent_id}' (ACP) marked cancelled (no cooperative checkpoint)."
    )
    return (
        f"Subagent '{agent_id}' marked as cancelled. "
        "The background thread will stop at its next step boundary."
    )


def subagent_steer(agent_id: str, message: str) -> str:
    """Inject a steering message into a running subagent's conversation.

    The message is queued via the subagent's logdir prompt-queue and picked up
    on the subagent's next chat loop iteration, allowing the orchestrator to
    redirect, clarify, or course-correct a subagent mid-run without restarting
    it. Works for thread-mode and subprocess-mode subagents.

    This is distinct from ``subagent_reply()``, which re-spawns a subagent that
    has *already stopped* with a ``clarification_needed`` status. Use this
    function to steer a subagent that is still actively running.

    Args:
        agent_id: The running subagent to steer.
        message: The guidance to inject. This will appear as a user turn in the
            subagent's conversation on its next loop iteration.

    Returns:
        A human-readable confirmation message.

    Raises:
        ValueError: If no subagent with ``agent_id`` is found, or if the
            subagent has already finished (use ``subagent_reply()`` for
            clarification-needed subagents).
        NotImplementedError: If the subagent is running in ACP mode, which
            does not expose a logdir channel for steering.

    Note:
        Delivery is **guaranteed for both thread-mode and subprocess-mode**
        subagents:

        - **Thread mode**: ``prompt_queue_closed`` is a Python event set inside
          the subagent thread immediately when ``chat()`` returns.
        - **Subprocess mode**: ``chat()`` writes a ``"prompt-queue-closed"``
          sentinel file to the logdir in its ``finally`` block — before the
          process exits — giving a child-side signal that closes the drain
          boundary precisely.  The parent-side ``prompt_queue_closed`` event
          (set after ``_monitor_subprocess`` returns) is a second-level catch.

        Any steer attempted after the sentinel/event is set raises
        ``ValueError``.

    Example::

        subagent("researcher", "Research Python async frameworks")
        # ... the researcher is going off-track ...
        subagent_steer("researcher", "Focus only on frameworks with >5k GitHub stars")
    """
    from ...prompt_queue import queue_prompt  # fmt: skip

    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)

    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id!r} not found.")

    if sa.execution_mode == "acp":
        raise NotImplementedError(
            f"Subagent '{agent_id}' is in ACP mode. Steering is not supported "
            "for ACP subagents — they run in a separate harness with no shared logdir channel."
        )

    def _queue_is_closed() -> bool:
        """Return True if the subagent's chat loop has finished draining.

        Both modes: chat.py writes a "prompt-queue-closed" sentinel file to
        the logdir at the actual drain boundary — right before the ``break``
        in ``_run_chat_loop`` (non-interactive exit) and right before raising
        ``SessionCompleteException`` — and again in ``chat()``'s ``finally``
        block as a safety net.  Checking this file for both thread and
        subprocess mode closes the race window between ``chat()``'s last
        drain and ``prompt_queue_closed.set()`` being called.

        Thread mode: the sentinel file is the primary, early signal.
        ``prompt_queue_closed`` is a Python event set immediately *after*
        ``chat()`` returns — slightly later than the file, so it is a
        second-level catch.

        Subprocess mode: the sentinel file is written by the child inside
        ``chat()``, before the process exits.  ``prompt_queue_closed`` is
        set by the parent launcher after the process exits — a second-level
        catch here too.

        Stale-sentinel guard: planner-mode subagents reuse the same logdir
        across runs (same ``agent_id`` → same ``subagent-{agent_id}``
        directory).  A sentinel written by a *previous* run persists on
        disk.  We ignore it by requiring the file's mtime to be no earlier
        than ``sa.started_at`` (seconds since epoch, set when the Subagent
        object is created at spawn time).
        """
        sentinel = sa.logdir / "prompt-queue-closed"
        sentinel_is_current = (
            sentinel.exists() and sentinel.stat().st_mtime >= sa.started_at
        )
        return sa.prompt_queue_closed.is_set() or sentinel_is_current

    # Check closed state first: sentinel file is written at the actual drain
    # boundary for both thread and subprocess modes (see _queue_is_closed).
    if _queue_is_closed():
        raise ValueError(
            f"Subagent '{agent_id}' has closed its prompt queue — "
            "its chat loop has finished and will not accept new steering messages."
        )

    pre_status = sa.status().status
    if pre_status != "running":
        raise ValueError(
            f"Subagent '{agent_id}' is not running (status: {pre_status}). "
            "Only active subagents can be steered. "
            "For clarification_needed subagents, use subagent_reply() to re-spawn them."
        )

    queue_prompt(sa.logdir, message)

    # Re-check after queuing: catches the race where the subagent exits between
    # the initial check and the queue write.
    if _queue_is_closed():
        raise ValueError(
            f"Subagent '{agent_id}' closed its prompt queue while the steering message "
            "was being queued. The message will not be processed."
        )
    post_status = sa.status().status
    if post_status != "running":
        raise ValueError(
            f"Subagent '{agent_id}' exited after steering message was queued. "
            f"The message may not be processed. Status: {post_status}"
        )

    logger.info(f"Steering message queued for subagent '{agent_id}': {message[:80]!r}")
    return (
        f"Steering message queued for subagent '{agent_id}'. "
        "It will be injected into the subagent's conversation on its next loop iteration."
    )


def subagent_reply(agent_id: str, reply: str) -> None:
    """Re-spawn a subagent that requested clarification.

    When a subagent ends with a ``clarify`` block, it stops and asks the
    parent a question. Call this function with your answer to re-start the
    subagent. The new run receives the original prompt plus an appended
    Q&A block so it has full context.

    Args:
        agent_id: The subagent that raised the clarification request.
        reply: Your answer to the subagent's question.
    """
    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)

    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id!r} not found.")

    result = sa.status()
    if result.status == "running":
        raise ValueError(
            f"Subagent '{agent_id}' is still running. Wait for it to finish first."
        )
    if result.status != "clarification_needed":
        raise ValueError(
            f"Subagent '{agent_id}' has status '{result.status}', not 'clarification_needed'. "
            "Only subagents that ended with a `clarify` block can be resumed."
        )

    # Guard against unbounded clarification loops
    _MAX_CLARIFICATIONS = 5
    clarification_count = sa.prompt.count("[Clarification from previous attempt]")
    if clarification_count >= _MAX_CLARIFICATIONS:
        raise ValueError(
            f"Subagent '{agent_id}' has requested clarification {clarification_count} times "
            f"(limit is {_MAX_CLARIFICATIONS}). "
            "Resolve the ambiguity in the task prompt instead of relying on further clarification."
        )

    question = result.result or "(no question)"
    augmented_prompt = (
        f"{sa.prompt}\n\n"
        f"[Clarification from previous attempt]\n"
        f"Q: {question}\n"
        f"A: {reply}"
    )

    # Atomically clear old state: save first so we can restore on failure.
    with _subagent_results_lock:
        old_result = _subagent_results.pop(agent_id, None)

    with _subagents_lock:
        _subagents[:] = [
            existing for existing in _subagents if existing.agent_id != agent_id
        ]

    # Re-spawn with the same parameters, augmented prompt.
    # On failure, restore the old state so the caller can retry.
    try:
        subagent(
            agent_id=agent_id,
            prompt=augmented_prompt,
            model=sa.model,
            context_mode=sa.context_mode,
            context_include=list(sa.context_include) if sa.context_include else None,
            output_schema=sa.output_schema,
            use_subprocess=sa.execution_mode == "subprocess",
            use_acp=sa.use_acp,
            acp_command=sa.acp_command or "gptme-acp",
            profile=sa.profile,
            workdir=sa.workdir,
            isolated=sa.isolated,
            timeout=sa.timeout,
            role=sa.role,
            redact_secrets=sa.redact_secrets,
            context_window=sa.context_window,
            max_time=sa.max_time,
            context_turns=sa.context_turns,
        )
    except Exception:
        with _subagents_lock:
            _subagents.append(sa)
        if old_result is not None:
            with _subagent_results_lock:
                _subagent_results[agent_id] = old_result
        raise


def subagent_list() -> list[dict]:
    """Returns a list of all subagents with their current status.

    Each entry contains:
    - agent_id: The subagent identifier
    - status: running/success/failure/clarification_needed
    - model: The model used (or None)
    - execution_mode: thread/subprocess/acp
    - elapsed_s: Seconds since the subagent started (from started_at timestamp)
    - prompt_preview: First 100 characters of the prompt

    Useful for:
    - Interactive sessions: "what's running right now?"
    - Orchestrators deciding whether to spawn more agents
    - Debugging runaway subagent fans
    """
    import time

    now = time.time()
    with _subagents_lock:
        agents = list(_subagents)  # copy under lock, then iterate outside

    result: list[dict[str, Any]] = []
    for sa in agents:
        status = sa.status().status

        # Estimate elapsed time from start time
        elapsed_s = int(now - sa.started_at)

        # Truncate prompt for preview
        prompt = sa.prompt[:97] + "..." if len(sa.prompt) > 100 else sa.prompt

        result.append(
            {
                "agent_id": sa.agent_id,
                "status": status,
                "model": sa.model,
                "execution_mode": sa.execution_mode,
                "elapsed_s": max(elapsed_s, 0),
                "prompt_preview": prompt,
            }
        )

    # Sort newest first (smallest elapsed_s = most recently started)
    result.sort(key=lambda x: x["elapsed_s"])
    return result


def subagent_status(agent_id: str) -> dict:
    """Returns the status of a subagent."""
    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)
    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")
    return asdict(sa.status())


def subagent_wait(
    agent_id: str, timeout: int = 60, max_result_chars: int = 2000
) -> dict:
    """Waits for a subagent to finish.

    Args:
        agent_id: The subagent to wait for
        timeout: Maximum seconds to wait (default 60)
        max_result_chars: Truncate result text to this many characters (default 2000).
            Long subagent outputs are truncated to keep the parent's context clean.
            Call subagent_read_log(agent_id) to read the full output.

    Returns:
        Status dict with 'status' and 'result' keys
    """
    # Use the most recently spawned entry — _subagents is append-only, so
    # reversed() finds the newest match when the same agent_id is reused.
    with _subagents_lock:
        sa = next((s for s in reversed(_subagents) if s.agent_id == agent_id), None)

    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")

    logger.info(f"Waiting for subagent {agent_id} to finish...")

    if sa.execution_mode == "subprocess" and sa.process:
        # Subprocess mode: wait for process
        try:
            sa.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(f"Subagent {agent_id} timed out after {timeout}s")
            sa.process.kill()
            sa.process.wait()  # reap the killed process
    elif sa.execution_mode == "acp" and sa.thread:
        # ACP mode: wait for the wrapper thread
        sa.thread.join(timeout=timeout)
        if sa.thread.is_alive():
            logger.warning(
                f"Subagent {agent_id} ACP thread still running after {timeout}s timeout"
                " — cannot cancel daemon thread, it will continue in background"
            )
    elif sa.thread:
        # Thread mode: join thread
        sa.thread.join(timeout=timeout)

    cached_status = (
        _wait_for_cached_subagent_result(agent_id) if sa.is_running() else None
    )
    status = cached_status if cached_status is not None else sa.status()
    result_dict = asdict(status)

    # Apply output_schema parsing before truncation — the parsed object may be
    # larger or smaller than the raw JSON string, so we measure truncation against
    # the final result text, not the pre-parse raw.
    if sa.output_schema is not None and result_dict.get("status") == "success":
        from .batch import (
            _parse_result,  # late import avoids circular (batch imports api)
        )

        result_dict = _parse_result(result_dict, sa.output_schema)

    # Compact result: truncate long outputs so they don't flood the parent's context.
    # The complete block is meant to be a brief summary; if it's longer than
    # max_result_chars the parent agent can call subagent_read_log() to get details.
    result_text = result_dict.get("result")
    if (
        isinstance(result_text, str)
        and max_result_chars > 0
        and len(result_text) > max_result_chars
    ):
        result_dict["result"] = (
            result_text[:max_result_chars]
            + f"\n... [truncated — call subagent_read_log('{agent_id}') for full output]"
        )

    return result_dict


def subagent_wait_any(
    agent_ids: list[str],
    timeout: int = 300,
) -> tuple[str, dict]:
    """Wait for the first of the given subagents to complete.

    Useful for speculative/hedging patterns: spawn N subagents and take
    whichever finishes first, then cancel the rest with ``subagent_cancel()``.

    Args:
        agent_ids: List of agent IDs to wait on.
        timeout: Maximum seconds to wait for any agent to complete.

    Returns:
        Tuple of ``(agent_id, result_dict)`` for the first agent that
        completes. ``result_dict`` has ``"status"`` (``"success"`` /
        ``"failure"`` / ``"clarification_needed"``) and ``"result"`` keys.

    Raises:
        ValueError: If ``agent_ids`` is empty.
        TimeoutError: If no agent completes within ``timeout`` seconds.

    Example::

        # Race pattern: take whichever approach finishes first
        subagent("fast", "Quick attempt at task X")
        subagent("thorough", "Thorough attempt at task X")
        first_id, result = subagent_wait_any(["fast", "thorough"], timeout=120)
        print(f"{first_id} won the race: {result['status']}")
        # Cancel the slower agent
        for aid in ("fast", "thorough"):
            if aid != first_id:
                subagent_cancel(aid)
    """
    if not agent_ids:
        raise ValueError("agent_ids must not be empty")

    from .batch import BatchJob

    job = BatchJob(agent_ids=list(agent_ids))
    return job.wait_any(timeout=timeout)


def subagent_read_log(
    agent_id: str,
    max_messages: int = 50,
    include_system: bool = False,
    message_filter: str | None = None,
) -> str:
    """Read the conversation log of a subagent.

    Args:
        agent_id: The subagent to read logs from
        max_messages: Maximum number of messages to return
        include_system: Whether to include system messages
        message_filter: Filter messages by role (user/assistant/system) or None for all

    Returns:
        Formatted log output showing the conversation
    """
    # Use the most recently spawned entry — _subagents is append-only, so
    # reversed() finds the newest match when the same agent_id is reused.
    with _subagents_lock:
        sa = next((s for s in reversed(_subagents) if s.agent_id == agent_id), None)

    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")

    try:
        log_manager = sa.get_log()
        messages = log_manager.log.messages

        # Filter messages
        if not include_system:
            messages = [m for m in messages if m.role != "system"]
        if message_filter:
            messages = [m for m in messages if m.role == message_filter]

        # Limit number of messages
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        # Format output
        output = f"=== Subagent Log: {agent_id} ===\n"
        output += f"Total messages: {len(messages)}\n"
        output += f"Logdir: {sa.logdir}\n\n"

        for msg in messages:
            timestamp = msg.timestamp.strftime("%H:%M:%S") if msg.timestamp else "N/A"
            content_preview = (
                msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
            )
            output += f"[{timestamp}] {msg.role}:\n{content_preview}\n\n"

        return output
    except Exception as e:
        return f"Error reading log: {e}\nLogdir: {sa.logdir}"
