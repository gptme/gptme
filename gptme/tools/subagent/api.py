"""Subagent public API — create, monitor, and manage subagents.

Contains the main subagent() function and supporting status/wait/read_log
functions that form the public interface of the subagent tool.
"""

import logging
import random
import string
import subprocess
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from . import execution as _exec
from .concurrency import get_slot_sem
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
)

logger = logging.getLogger(__name__)


def subagent(
    agent_id: str,
    prompt: str,
    mode: Literal["executor", "planner"] = "executor",
    subtasks: list[SubtaskDef] | None = None,
    execution_mode: Literal["parallel", "sequential"] = "parallel",
    context_mode: Literal["full", "selective"] = "full",
    context_include: list[str] | None = None,
    output_schema: type | None = None,
    use_subprocess: bool | None = None,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    profile: str | None = None,
    model: str | None = None,
    isolated: bool | None = None,
    timeout: int = 1800,
    role: Role | None = None,
    redact_secrets: bool = True,
    context_window: int | None = None,
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

    if mode == "planner":
        if not subtasks:
            raise ValueError("Planner mode requires subtasks parameter")
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
        )

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

        def run_acp_subagent():
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

                    result = ReturnType(status, summary)
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
                        _exec._cleanup_isolation(sa_ref)
            finally:
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
            isolated=isolated,
            worktree_path=worktree_path,
            repo_path=repo_path,
            role=role,
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
        if profile:
            logger.info(f"  with profile: {profile}")
        # Convert output_schema type to JSON string if present
        output_schema_str = None
        if output_schema is not None:
            import json

            # Convert pydantic model or type to JSON schema string
            if hasattr(output_schema, "model_json_schema"):
                output_schema_str = json.dumps(output_schema.model_json_schema())
            elif hasattr(output_schema, "__annotations__"):
                # TypedDict or dataclass - create simple schema
                output_schema_str = json.dumps({"type": "object"})

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
            isolated=isolated,
            worktree_path=worktree_path,
            repo_path=repo_path,
            timeout=timeout,
            role=role,
        )
        with _subagents_lock:
            _subagents.append(sa)
        launcher.start()
    else:
        # Thread mode: original behavior, gated by the concurrency semaphore.
        # The semaphore is acquired before starting LLM work and released in
        # finally so excess agents queue until a slot opens.
        def run_subagent():
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
                    # Use _read_log() instead of status(): the thread is still alive here,
                    # so status() would return "running" and poison the result cache.
                    result = sa._read_log()
                    if not set_subagent_result_if_absent(agent_id, result):
                        _exec._cleanup_isolation(sa)
                        return
                    try:
                        summary = _exec._summarize_result(result, max_chars=200)
                        notify_completion(agent_id, result.status, summary)
                    except Exception as e:
                        logger.warning(f"Failed to notify subagent completion: {e}")
                    # Clean up worktree isolation
                    _exec._cleanup_isolation(sa)
            finally:
                _sem.release()

        # Create thread (don't start yet)
        t = threading.Thread(
            target=run_subagent,
            daemon=True,
        )

        # Register Subagent BEFORE starting thread to avoid race condition:
        # run_subagent closure looks up agent_id in _subagents, which would
        # return None if the thread runs before _subagents.append(sa).
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
            isolated=isolated,
            worktree_path=worktree_path,
            repo_path=repo_path,
            role=role,
            redact_secrets=redact_secrets,
            context_window=context_window,
        )
        with _subagents_lock:
            _subagents.append(sa)
        t.start()


def subagent_cancel(agent_id: str) -> str:
    """Cancel a running subagent.

    For subprocess-mode subagents, sends SIGTERM (then SIGKILL after 5s) to the
    process. For thread-mode subagents, marks the result as cancelled — the thread
    continues until its next natural checkpoint but the result is already recorded
    as failure so callers won't block waiting for it.

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

    cancelled_result = ReturnType("failure", "Cancelled by orchestrator")

    if sa.execution_mode == "subprocess" and sa.process:
        if not set_subagent_result_if_absent(agent_id, cancelled_result):
            return f"Subagent '{agent_id}' already finished before cancellation."
        sa.process.terminate()
        try:
            sa.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sa.process.kill()
            sa.process.wait()
        logger.info(f"Subagent '{agent_id}' subprocess terminated.")
        return f"Subagent '{agent_id}' cancelled."
    # Thread/ACP mode: threads cannot be forcefully stopped in Python.
    # Mark the result so the orchestrator sees it as cancelled immediately.
    if not set_subagent_result_if_absent(agent_id, cancelled_result):
        return f"Subagent '{agent_id}' already finished before cancellation."
    logger.info(
        f"Subagent '{agent_id}' marked cancelled (thread will stop at next checkpoint)."
    )
    return (
        f"Subagent '{agent_id}' marked as cancelled. "
        "The background thread will stop at its next natural checkpoint."
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
            isolated=sa.isolated,
            timeout=sa.timeout,
            role=sa.role,
            redact_secrets=sa.redact_secrets,
            context_window=sa.context_window,
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
    sa = None
    with _subagents_lock:
        for s in _subagents:
            if s.agent_id == agent_id:
                sa = s
                break

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

    status = sa.status()
    result_dict = asdict(status)

    # Compact result: truncate long outputs so they don't flood the parent's context.
    # The complete block is meant to be a brief summary; if it's longer than
    # max_result_chars the parent agent can call subagent_read_log() to get details.
    result_text = result_dict.get("result")
    if result_text and len(result_text) > max_result_chars:
        result_dict["result"] = (
            result_text[:max_result_chars]
            + f"\n... [truncated — call subagent_read_log('{agent_id}') for full output]"
        )

    return result_dict


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
    sa = None
    with _subagents_lock:
        for s in _subagents:
            if s.agent_id == agent_id:
                sa = s
                break

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
