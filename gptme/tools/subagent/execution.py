"""Subagent execution backends — thread, subprocess, and process monitoring.

Extracted from the main subagent module to separate execution logic (how
subagents are spawned and monitored) from the public API and tool registration.

Functions here are internal implementation details called by the main
subagent() function in api.py.
"""

import logging
import random
import string
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ...message import Message
from .. import get_tools, set_tools

if TYPE_CHECKING:
    from .types import ReturnType, Status, Subagent, SubtaskDef

logger = logging.getLogger(__name__)


def _load_agent_memory(profile_name: str | None) -> tuple[str | None, Path | None]:
    """Load persistent memory for an agent profile.

    Memory is currently scoped per-profile (global to all projects using that profile).

    # TODO: Evaluate project-specific memory scoping
    # Current: profile-global (all projects share one MEMORY.md per profile)
    # Alternative: per-project scoping (e.g. hash of workspace path, like Claude Code)
    # Concern: path-hashing is brittle in worktrees / when workspace moves.
    # Keeping profile-global for now; revisit if users need project isolation.

    Args:
        profile_name: Name of the agent profile

    Returns:
        Tuple of (memory_content, memory_dir) or (None, None) if no profile
    """
    if not profile_name:
        return None, None

    from ...dirs import get_profile_memory_dir

    memory_dir = get_profile_memory_dir(profile_name)
    memory_file = memory_dir / "MEMORY.md"

    if memory_file.exists():
        try:
            content = memory_file.read_text().strip()
            if content:
                return content, memory_dir
        except Exception as e:
            logger.warning(f"Failed to read profile memory for '{profile_name}': {e}")

    return None, memory_dir


def _build_memory_system_message(
    memory_content: str | None, memory_dir: Path
) -> Message:
    """Build a system message with memory context for a subagent.

    Args:
        memory_content: Existing memory content (or None if empty)
        memory_dir: Path to the memory directory
    """
    parts = [
        f"# Agent Memory\n\nYour persistent memory directory is at `{memory_dir}/`."
    ]

    if memory_content:
        parts.append(f"## Current Memory\n\nContents of MEMORY.md:\n\n{memory_content}")
    else:
        parts.append("Your memory is currently empty.")

    parts.append(
        "## Saving Memories\n\n"
        "You can save learnings that persist across sessions by writing to "
        f"`{memory_dir}/MEMORY.md`. Use this to remember:\n"
        "- Patterns and conventions discovered in this project\n"
        "- Key file paths and architecture decisions\n"
        "- Solutions to recurring problems\n\n"
        "Keep MEMORY.md concise (under 200 lines). "
        "Create separate files in the memory directory for detailed notes."
    )

    return Message("system", "\n\n".join(parts))


def _create_subagent_thread(
    prompt: str,
    logdir: Path,
    model: str | None,
    context_mode: Literal["full", "selective"],
    context_include: list[str] | None,
    workspace: Path,
    target: str = "parent",
    output_schema: type | None = None,
    profile_name: str | None = None,
) -> None:
    """Shared function for running subagent threads.

    Args:
        prompt: Task prompt for the subagent
        logdir: Directory for conversation logs
        model: Model to use for the subagent
        context_mode: Controls what context is shared
        context_include: For selective mode, list of context components
        workspace: Workspace directory
        target: Who will review the results ("parent" or "planner")
        profile_name: Optional agent profile to apply (system prompt + hard tool enforcement)
    """
    # noreorder
    from gptme import chat  # fmt: skip
    from gptme.executor import prepare_execution_environment  # fmt: skip
    from gptme.llm.models import set_default_model  # fmt: skip

    from ...profiles import get_profile  # fmt: skip
    from ...prompts import get_prompt  # fmt: skip
    from .hooks import _get_complete_instruction  # fmt: skip

    # Resolve profile if specified
    profile = get_profile(profile_name) if profile_name else None
    if profile_name and not profile:
        logger.warning(f"Unknown profile '{profile_name}', ignoring")

    # Initialize model and tools for this thread
    if model:
        set_default_model(model)

    # Apply profile tool restrictions if specified
    tool_allowlist = None
    if profile and profile.tools is not None:
        tool_allowlist = profile.tools

    prepare_execution_environment(workspace=workspace, tools=None)

    # Get tools, filtered by profile if applicable
    if tool_allowlist is not None:
        loaded_tools = get_tools()
        loaded_names = {t.name for t in loaded_tools}
        # Warn about unknown tool names in profile (typos, missing extras)
        unknown = set(tool_allowlist) - loaded_names
        if unknown:
            logger.warning(
                "Profile '%s' references unknown tools: %s (available: %s)",
                profile.name if profile else "?",
                ", ".join(sorted(unknown)),
                ", ".join(sorted(loaded_names)),
            )
        available_tools = [t for t in loaded_tools if t.name in tool_allowlist]
        # Always include the complete tool so subagent can signal completion
        complete_tools = [t for t in loaded_tools if t.name == "complete"]
        for ct in complete_tools:
            if ct not in available_tools:
                available_tools.append(ct)
        # Hard enforcement: replace loaded tools so execute_msg() only sees allowed tools
        set_tools(available_tools)
    else:
        available_tools = get_tools()

    prompt_msgs = [Message("user", prompt)]

    # Build initial messages based on context_mode
    if context_mode == "selective":
        # Selective context - build from specified components
        from ...prompts import prompt_gptme, prompt_tools

        initial_msgs = []

        # Type narrowing: context_include validated as not None by caller
        assert context_include is not None

        # Add components based on context_include
        if "agent" in context_include:
            initial_msgs.extend(list(prompt_gptme(False, None, agent_name=None)))
        if "tools" in context_include:
            initial_msgs.extend(
                list(prompt_tools(tools=available_tools, tool_format="markdown"))
            )
    else:  # "full" mode (default)
        # Full context (using profile-filtered tools)
        initial_msgs = get_prompt(
            available_tools, interactive=False, workspace=workspace
        )

    # Append profile system prompt if specified
    if profile and profile.system_prompt:
        profile_msg = Message(
            "system",
            f"# Agent Profile: {profile.name}\n\n{profile.system_prompt}",
        )
        initial_msgs.append(profile_msg)

    # Load and inject persistent memory for this profile
    memory_content, memory_dir = _load_agent_memory(profile_name)
    if memory_dir is not None:
        memory_msg = _build_memory_system_message(memory_content, memory_dir)
        initial_msgs.append(memory_msg)

    # Add completion instruction as a system message
    complete_instruction = Message(
        "system",
        _get_complete_instruction(target),
    )
    initial_msgs.append(complete_instruction)

    # Note: workspace parameter is always passed to chat() (required parameter)
    # Workspace context in messages is controlled by initial_msgs
    chat(
        prompt_msgs,
        initial_msgs,
        logdir=logdir,
        workspace=workspace,
        model=model,
        stream=False,
        no_confirm=True,
        interactive=False,
        show_hidden=False,
        tool_format="markdown",
        output_schema=output_schema,
    )


def _run_subagent_subprocess(
    prompt: str,
    logdir: Path,
    model: str | None,
    workspace: Path,
    context_mode: Literal["full", "selective"] | None = None,
    context_include: list[str] | None = None,
    output_schema: str | None = None,
    profile: str | None = None,
) -> subprocess.Popen:
    """Run a subagent in a subprocess for output isolation.

    This provides better isolation than threads - subprocess stdout/stderr
    doesn't mix with the parent's output.

    Args:
        prompt: Task prompt for the subagent
        logdir: Directory for conversation logs
        model: Model to use (or None for default)
        workspace: Workspace directory
        context_mode: Context mode (full or selective)
        context_include: Context components to include for selective mode.
            "workspace" maps to the CLI's "files" context. Legacy values like
            "files", "cmd", and "all" are still accepted in subprocess mode.
            "agent" and "tools" are ignored here because the CLI already
            includes them.
        output_schema: JSON schema for structured output
        profile: Agent profile name to apply via --agent-profile flag

    Returns:
        The subprocess.Popen object for monitoring
    """
    from .hooks import _get_complete_instruction

    cmd = [
        sys.executable,
        "-m",
        "gptme",
        "-n",  # Non-interactive
        "--no-confirm",
        f"--name={logdir.name}",  # Just the folder name, not full path
    ]

    if model:
        cmd.extend(["--model", model])

    if profile:
        cmd.extend(["--agent-profile", profile])

    # Map context_mode/context_include to the --context CLI flag
    if context_mode == "selective" and context_include:
        # Map internal component names to CLI --context values
        # Thread mode handles "agent"/"tools" internally; for subprocess mode,
        # map to CLI-compatible values ("files", "cmd").
        cli_values = []
        for component in context_include:
            if component in ("files", "cmd", "all"):
                cli_values.append(component)
            elif component == "workspace":
                cli_values.append("files")
            elif component in ("agent", "tools"):
                # "agent" and "tools" are always included by the CLI,
                # no need to pass them explicitly
                pass
            else:
                logger.warning(f"Unknown context_include component: {component}")
        if cli_values:
            for val in cli_values:
                cmd.extend(["--context", val])

    if output_schema:
        cmd.extend(["--output-schema", output_schema])

    # Load persistent memory and prepend to prompt for subprocess mode
    memory_content, memory_dir = _load_agent_memory(profile)
    if memory_dir is not None:
        memory_section = f"\n\n[Agent Memory - stored at {memory_dir}/MEMORY.md]\n"
        if memory_content:
            memory_section += f"{memory_content}\n"
        else:
            memory_section += "No memories saved yet.\n"
        memory_section += (
            "You can save learnings to your memory by writing to "
            f"{memory_dir}/MEMORY.md\n"
        )
        prompt = prompt + memory_section

    # Add completion instruction to the prompt for subprocess mode
    # (In thread mode, this is added as a system message)
    complete_section = (
        f"\n\n[Completion Instructions]\n{_get_complete_instruction('orchestrator')}\n"
    )
    prompt = prompt + complete_section

    # Pass prompt via stdin (piped from a temp file) instead of as a CLI argument.
    # This avoids ARG_MAX limits for large prompts and keeps argv clean.
    # gptme reads stdin when it's not a TTY and uses the content as the prompt.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="gptme-prompt-"
    ) as tmpf:
        tmpf.write(prompt)
        tmpfile_path = Path(tmpf.name)

    try:
        with open(tmpfile_path) as stdin_file:
            process = subprocess.Popen(
                cmd,
                stdin=stdin_file,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=workspace,
                text=True,
            )
    finally:
        tmpfile_path.unlink(missing_ok=True)

    return process


def _summarize_result(result: "ReturnType", max_chars: int = 200) -> str:
    """Create a token-efficient summary of a subagent result.

    Args:
        result: The subagent's ReturnType
        max_chars: Maximum characters in the summary

    Returns:
        A brief summary suitable for notification messages
    """
    if result.result is None:
        return f"Status: {result.status}"

    text = str(result.result)

    # If it's short enough, return as-is
    if len(text) <= max_chars:
        return text

    # Truncate with ellipsis
    return text[: max_chars - 3] + "..."


def _cleanup_isolation(subagent: "Subagent") -> None:
    """Clean up worktree or temp directory after subagent completes."""
    if not subagent.isolated or not subagent.worktree_path:
        return

    from ...util.git_worktree import cleanup_worktree

    try:
        cleanup_worktree(subagent.worktree_path, subagent.repo_path)
    except Exception as e:
        logger.warning(f"Failed to cleanup isolation for {subagent.agent_id}: {e}")


def _monitor_subprocess(
    subagent: "Subagent",
) -> None:
    """Monitor a subprocess and invoke callbacks when it completes.

    Runs in a background thread to enable non-blocking operation.
    Subprocess stdout/stderr are sent to DEVNULL since results are read
    from the conversation log, not the process pipes.
    """
    from .hooks import notify_completion
    from .types import (
        ReturnType,
        _subagent_results,
        _subagent_results_lock,
    )

    if not subagent.process:
        return

    # Track whether the process was killed due to our timeout (vs external SIGKILL)
    _timed_out = False

    # Wait for process to complete with timeout to prevent indefinite blocking
    try:
        subagent.process.wait(timeout=subagent.timeout)
    except subprocess.TimeoutExpired:
        logger.warning(
            f"Subagent {subagent.agent_id} timed out after {subagent.timeout}s, killing"
        )
        _timed_out = True
        subagent.process.kill()
        subagent.process.wait()  # reap the killed process

    # Determine status based on return code
    if subagent.process.returncode == 0:
        status: Status = "success"
        # Get result from conversation log (primary source for subprocess mode)
        try:
            log_status = subagent.status()
            result = log_status.result
        except Exception:
            result = "Task completed (check log for details)"
    elif _timed_out:
        # Process was killed because our timeout expired (not an external SIGKILL)
        status = "failure"
        result = f"Process killed after {subagent.timeout}s timeout"
    else:
        status = "failure"
        result = f"Process exited with code {subagent.process.returncode}"

    # Cache the result in module-level dict (Subagent is frozen)
    # Use lock for thread-safe access when multiple subagents run in parallel
    final_result = ReturnType(status, result)
    with _subagent_results_lock:
        _subagent_results[subagent.agent_id] = final_result

    # Notify via hook system (fire-and-forget-then-get-alerted pattern)
    try:
        summary = _summarize_result(final_result, max_chars=200)
        notify_completion(subagent.agent_id, status, summary)
    except Exception as e:
        logger.warning(f"Failed to notify subagent completion: {e}")

    # Clean up worktree isolation
    _cleanup_isolation(subagent)


def _run_planner(
    agent_id: str,
    prompt: str,
    subtasks: "list[SubtaskDef]",
    execution_mode: Literal["parallel", "sequential"] = "parallel",
    context_mode: Literal["full", "selective"] = "full",
    context_include: list[str] | None = None,
    model: str | None = None,
    profile_name: str | None = None,
    plan: dict | None = None,
) -> None:
    """Run a planner that delegates work to multiple executor subagents.

    Args:
        agent_id: Identifier for the planner
        prompt: Context prompt shared with all executors
        subtasks: List of subtask definitions to execute
        execution_mode: "parallel" (all at once) or "sequential" (one by one)
        context_mode: Controls what context is shared with executors (see subagent() docs)
        context_include: For selective mode, list of context components to include
        profile_name: Agent profile to apply to executor subagents
        plan: Internal plan dict (for subagent spawning relay)
    """
    from gptme.cli.main import get_logdir

    from .types import Subagent, _subagents, _subagents_lock, resolve_role_defaults

    logger.info(
        f"Starting planner {agent_id} with {len(subtasks)} subtasks "
        f"in {execution_mode} mode"
    )

    def random_string(n):
        s = string.ascii_lowercase + string.digits
        return "".join(random.choice(s) for _ in range(n))

    for subtask in subtasks:
        executor_id = f"{agent_id}-{subtask['id']}"
        executor_prompt = f"Context: {prompt}\n\nSubtask: {subtask['description']}"
        name = f"subagent-{executor_id}"
        logdir = get_logdir(name + "-" + random_string(4))

        # Resolve role-based profile per subtask if role is set
        subtask_role = subtask.get("role")
        resolved_profile = profile_name
        if subtask_role:
            # NOTE(phase2): use_subprocess/isolated from role are not yet forwarded
            # to individual executors (planner always uses thread mode). Only profile
            # is applied here. See SubtaskDef.role docstring for the Phase 2 plan.
            _, _, role_profile = resolve_role_defaults(subtask_role, None, None)
            if role_profile is not None and profile_name is None:
                resolved_profile = role_profile
                logger.info(
                    f"Subtask '{subtask['id']}' resolved profile '{role_profile}' from role='{subtask_role}'"
                )

        # Capture workspace before spawning thread to avoid FileNotFoundError
        # if cwd is deleted (e.g., tmpdir cleanup in tests)
        try:
            workspace = Path.cwd()
        except FileNotFoundError:
            workspace = logdir.parent

        def run_executor(
            prompt=executor_prompt,
            log_dir=logdir,
            ws=workspace,
            subtask_profile=resolved_profile,
        ):
            _create_subagent_thread(
                prompt=prompt,
                logdir=log_dir,
                model=model,
                context_mode=context_mode,
                context_include=context_include,
                workspace=ws,
                target="planner",
                profile_name=subtask_profile,
            )

        t = threading.Thread(target=run_executor, daemon=True)
        # Register subagent BEFORE starting thread to avoid race condition
        # (matches pattern in api.py — thread closure may look up _subagents)
        with _subagents_lock:
            _subagents.append(Subagent(executor_id, executor_prompt, t, logdir, model))
        t.start()

        # Sequential mode: wait for each task to complete before starting next
        if execution_mode == "sequential":
            logger.info(f"Waiting for {executor_id} to complete (sequential mode)")
            t.join()
            logger.info(f"Executor {executor_id} completed")

    # Parallel mode: all threads already started
    if execution_mode == "parallel":
        logger.info(f"Planner {agent_id} spawned {len(subtasks)} executor subagents")
    else:
        logger.info(
            f"Planner {agent_id} completed {len(subtasks)} subtasks sequentially"
        )
