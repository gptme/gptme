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
from typing import Literal

from . import execution as _exec
from .hooks import notify_completion
from .types import (
    ReturnType,
    Subagent,
    SubtaskDef,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
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
    use_subprocess: bool = False,
    use_acp: bool = False,
    acp_command: str = "gptme-acp",
    profile: str | None = None,
    model: str | None = None,
    isolated: bool = False,
):
    """Starts an asynchronous subagent. Returns None immediately.

    Subagent completions are delivered via the LOOP_CONTINUE hook, enabling
    a "fire-and-forget-then-get-alerted" pattern where the orchestrator can
    continue working and get notified when subagents finish.

    Profile auto-detection: If ``agent_id`` matches a known profile name
    (e.g. "explorer", "researcher", "developer") or a common role alias
    ("explore"→"explorer", "research"→"researcher", "impl"/"dev"→"developer"),
    the profile is applied automatically — no need to pass ``profile`` separately.

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
            - "files": Project config files (gptme.toml files list)
            - "cmd": Dynamic context_cmd output
            - "all": Include both files and cmd
            Note: Tools and agent identity are always included by the CLI.
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
            Built-in profiles: default, explorer, researcher, developer, isolated, computer-use, browser-use.
            If not set, auto-detected from agent_id when it matches a profile name.
        model: Model to use for the subagent. Overrides parent's model.
            Useful for routing cheap tasks to faster/cheaper models.
        isolated: If True, run the subagent in a git worktree for filesystem
            isolation. The subagent gets its own copy of the repository and
            can modify files without affecting the parent. The worktree is
            automatically cleaned up after the subagent completes.
            Falls back to a temporary directory if not in a git repo.

    Returns:
        None: Starts asynchronous execution.
            In executor mode, starts a single task execution.
            In planner mode, starts execution of all subtasks using the specified execution_mode.

            Executors use the `complete` tool to signal completion with a summary.
            The full conversation log is available at the logdir path.
    """
    # noreorder
    from gptme.cli.main import get_logdir  # fmt: skip
    from gptme.llm.models import get_default_model  # fmt: skip

    from ...profiles import get_profile as _get_profile  # fmt: skip

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
            }
            aliased_profile = profile_aliases.get(agent_id)
            if aliased_profile and _get_profile(aliased_profile) is not None:
                profile = aliased_profile
                logger.info(
                    f"Auto-detected profile '{profile}' from agent_id alias '{agent_id}'"
                )

    # Determine model: explicit parameter > parent's model
    model_name: str | None
    if model:
        model_name = model
    else:
        current_model = get_default_model()
        model_name = current_model.full if current_model else None

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
                                chunk.get("text") if isinstance(chunk, dict) else None
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
                    result_text = "".join(collected_text) if collected_text else None

                    status = "success" if stop_reason == "end_turn" else "failure"
                    summary = (
                        result_text[:500]
                        if result_text
                        else f"ACP stop_reason={stop_reason}"
                    )
                    return status, summary

            try:
                status, summary = asyncio.run(_acp_run())

                with _subagent_results_lock:
                    _subagent_results[agent_id] = ReturnType(status, summary)
                notify_completion(
                    agent_id,
                    status,
                    _exec._summarize_result(ReturnType(status, summary), max_chars=200),
                )
            except Exception as e:
                logger.error(f"ACP subagent {agent_id} failed: {e}", exc_info=True)
                with _subagent_results_lock:
                    _subagent_results[agent_id] = ReturnType("failure", str(e))
                notify_completion(agent_id, "failure", f"ACP error: {e}")
            finally:
                with _subagents_lock:
                    sa_ref = next(
                        (s for s in _subagents if s.agent_id == agent_id), None
                    )
                if sa_ref:
                    _exec._cleanup_isolation(sa_ref)

        t = threading.Thread(target=run_acp_subagent, daemon=True)

        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=t,
            logdir=logdir,
            model=model_name,
            output_schema=output_schema,
            process=None,
            execution_mode="acp",
            acp_command=acp_command,
            isolated=isolated,
            worktree_path=worktree_path,
            repo_path=repo_path,
        )
        # Append sa before starting the thread so the finally block can find it
        # (avoids race condition where fast completion can't locate sa in _subagents)
        with _subagents_lock:
            _subagents.append(sa)
        t.start()

    elif use_subprocess:
        # Subprocess mode: better output isolation
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

        # Create Subagent with subprocess reference
        sa = Subagent(
            agent_id=agent_id,
            prompt=prompt,
            thread=None,
            logdir=logdir,
            model=model_name,
            output_schema=output_schema,
            process=process,
            execution_mode="subprocess",
            isolated=isolated,
            worktree_path=worktree_path,
            repo_path=repo_path,
        )
        with _subagents_lock:
            _subagents.append(sa)

        # Start monitor thread for hook-based completion notification
        monitor_thread = threading.Thread(
            target=_exec._monitor_subprocess,
            args=(sa,),
            daemon=True,
        )
        monitor_thread.start()
    else:
        # Thread mode: original behavior
        def run_subagent():
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
                )
            except Exception as e:
                # If subagent creation fails, notify with error status
                logger.error(f"Subagent {agent_id} failed during execution: {e}")
                try:
                    notify_completion(agent_id, "failure", f"Execution failed: {e}")
                except Exception as notify_err:
                    logger.warning(f"Failed to notify subagent error: {notify_err}")
                # Clean up worktree isolation even on failure
                with _subagents_lock:
                    sa = next((s for s in _subagents if s.agent_id == agent_id), None)
                if sa:
                    _exec._cleanup_isolation(sa)
                return

            # Notify via hook system when complete (only if successful)
            with _subagents_lock:
                sa = next((s for s in _subagents if s.agent_id == agent_id), None)
            if sa:
                result = sa.status()
                try:
                    summary = _exec._summarize_result(result, max_chars=200)
                    notify_completion(agent_id, result.status, summary)
                except Exception as e:
                    logger.warning(f"Failed to notify subagent completion: {e}")
                # Clean up worktree isolation
                _exec._cleanup_isolation(sa)

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
            output_schema=output_schema,
            process=None,
            execution_mode="thread",
            isolated=isolated,
            worktree_path=worktree_path,
            repo_path=repo_path,
        )
        with _subagents_lock:
            _subagents.append(sa)
        t.start()


def subagent_status(agent_id: str) -> dict:
    """Returns the status of a subagent."""
    with _subagents_lock:
        sa = next((s for s in _subagents if s.agent_id == agent_id), None)
    if sa is None:
        raise ValueError(f"Subagent with ID {agent_id} not found.")
    return asdict(sa.status())


def subagent_wait(agent_id: str, timeout: int = 60) -> dict:
    """Waits for a subagent to finish.

    Args:
        agent_id: The subagent to wait for
        timeout: Maximum seconds to wait (default 60)

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
    return asdict(status)


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
