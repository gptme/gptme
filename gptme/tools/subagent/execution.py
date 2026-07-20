"""Subagent execution backends — thread, subprocess, and process monitoring.

Extracted from the main subagent module to separate execution logic (how
subagents are spawned and monitored) from the public API and tool registration.

Functions here are internal implementation details called by the main
subagent() function in api.py.
"""

import logging
import os
import random
import string
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ...llm.retry_abort import bind_thread_generation, release_thread
from ...message import Message
from ...prompt_queue import drain_steer_prompts
from .. import clear_tools, get_tools, load_tool, set_tools
from .._allowlist import (
    is_hint_pattern,
    matching_allowlist_tools,
    tool_matches_allowlist,
)
from .concurrency import get_slot_sem
from .hooks import notify_completion, notify_progress
from .types import (
    ReturnType,
    set_subagent_result_if_absent,
    update_subagent_result_with_branch,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .types import Status, Subagent, SubtaskDef

logger = logging.getLogger(__name__)

_SUBAGENT_SIGNAL_TOOLS = ("complete", "clarify", "progress")

# Thread-local storage for subagent context
# Used by the progress tool to know which agent_id it's running as
_thread_local = threading.local()


def get_current_agent_id() -> str | None:
    """Return the agent_id of the currently running subagent, or None.

    Thread-mode subagents: set via _create_subagent_thread (thread-local).
    Subprocess-mode subagents: set via GPTME_SUBAGENT_AGENT_ID env var.
    """
    return getattr(_thread_local, "agent_id", None) or os.environ.get(
        "GPTME_SUBAGENT_AGENT_ID"
    )


def _ensure_subagent_signal_tools_loaded() -> None:
    """Load disabled-by-default signal tools needed by every subagent."""
    loaded_names = {tool.name for tool in get_tools()}
    for tool_name in _SUBAGENT_SIGNAL_TOOLS:
        if tool_name not in loaded_names:
            load_tool(tool_name)
            loaded_names.add(tool_name)


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


def _build_parent_context_message(parent_messages: list[Message]) -> Message:
    """Format parent conversation messages into a system context message for the subagent."""
    lines = ["# Parent Conversation Context\n"]
    for msg in parent_messages:
        role_label = msg.role.capitalize()
        lines.append(f"**{role_label}:**\n\n{msg.content}\n")
    lines.append(
        "\n_This context is provided so you can understand what the parent agent has been doing. "
        "Focus on your own task — don't re-execute work the parent has already completed._"
    )
    return Message("system", "\n".join(lines))


def _create_subagent_thread(
    prompt: str,
    logdir: Path,
    model: str | None,
    context_mode: Literal["full", "selective"],
    context_include: list[str] | None,
    workspace: Path,
    target: str = "parent",
    output_schema: "type | dict | None" = None,
    profile_name: str | None = None,
    agent_id: str | None = None,
    redact_secrets: bool = True,
    context_window: int | None = None,
    parent_messages: list[Message] | None = None,
    prompt_queue_closed: threading.Event | None = None,
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
        agent_id: Identifier stored in thread-local so the progress tool can self-identify
        context_window: Limit workspace context messages. None = no limit; 0 = minimal
            context (just agent identity + tools, no workspace files); N > 0 = at most
            N workspace context messages included.
        parent_messages: Recent messages from the parent's conversation to inject as context.
            Formatted as a system message so the subagent understands what the parent has
            been doing without confusing the subagent's own conversation flow.
        prompt_queue_closed: Optional event to set immediately when chat() returns.
            Passed by run_subagent() so the event fires before the caller acquires
            _subagents_lock to find sa — closing the lock+lookup window.
    """
    # Store agent_id in thread-local so the progress tool can identify this subagent
    if agent_id is not None:
        _thread_local.agent_id = agent_id

    # Start this subagent thread with a known-empty tool list.
    #
    # NOTE: in standard GIL-enabled Python builds (3.10–3.13 default) a plain
    # threading.Thread does NOT copy the parent's context — a new thread begins
    # with a fresh, empty contextvars context, so _loaded_tools_var here already
    # reads its default (None), independent of the parent's list.
    # Exception: Python 3.13 free-threaded builds (python3.13t, PEP 703) DO
    # copy the parent's context at thread-creation time, so in that build the
    # child would initially share the parent's _loaded_tools_var list object.
    # clear_tools() below handles both cases: it rebinds _loaded_tools_var to a
    # fresh list in the current context, so the parent's list is never mutated
    # regardless of whether the context was copied or not.
    # (Other copy paths: contextvars.copy_context() / asyncio tasks / thread
    # pools that explicitly run under a copied context.)
    # So on the common GIL-enabled path this clear_tools() is a defensive no-op;
    # on free-threaded 3.13t it is the load-bearing isolation step.
    #
    # It is kept as belt-and-suspenders in case this function is ever invoked
    # under a *copied* parent context (e.g. a future spawn path, or the server's
    # copy_context() step threads): clear_tools() rebinds _loaded_tools_var to a
    # fresh list in the current context, so the parent's list is never mutated.
    #
    # This does NOT explain the historical #554 "transient non-runnable" symptom:
    # the loaded-tools list is only ever appended to in place or replaced via
    # .set() (a context-local rebind) — never cleared/removed in place — so an
    # already-loaded parent tool cannot be made non-runnable by any concurrent
    # context. The crash is handled mechanism-agnostically in execute_msg (#3072).
    clear_tools()

    # noreorder
    from gptme.chat import chat  # fmt: skip
    from gptme.executor import prepare_execution_environment  # fmt: skip
    from gptme.llm.models import set_default_model  # fmt: skip

    from ...profiles import get_profile  # fmt: skip
    from ...prompts import get_prompt  # fmt: skip
    from .hooks import (
        _get_complete_instruction,  # fmt: skip
        _subagent_control_hook,  # fmt: skip
    )

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
    _ensure_subagent_signal_tools_loaded()

    # Register the control hook in this child thread's ContextVar context.
    # Hook registrations use a ContextVar — child threads start with an empty
    # registry and do not inherit the parent's registrations, so registering
    # at module level is not sufficient: the child never sees those hooks.
    from ...hooks import HookType, register_hook  # fmt: skip

    register_hook("subagent-control", HookType.STEP_PRE, _subagent_control_hook, 0)

    # Get tools, filtered by profile if applicable
    if tool_allowlist is not None:
        loaded_tools = get_tools()
        loaded_names = {t.name for t in loaded_tools}
        # Warn about unknown tool names in profile (typos, missing extras).
        # Skip hint: patterns — they're always valid (match by capability tag, not name).
        unknown = {
            pattern
            for pattern in tool_allowlist
            if not is_hint_pattern(pattern)
            and not matching_allowlist_tools(pattern, loaded_tools)
        }
        if unknown:
            logger.warning(
                "Profile '%s' references unknown tools: %s (available: %s)",
                profile.name if profile else "?",
                ", ".join(sorted(unknown)),
                ", ".join(sorted(loaded_names)),
            )
        available_tools = [
            tool
            for tool in loaded_tools
            if tool_matches_allowlist(tool.name, tool_allowlist, tool.hints)
        ]
        # Always include completion/clarification signal tools so restricted
        # subagents can still end cleanly or ask the parent for more context.
        required_tools = [
            tool for tool in loaded_tools if tool.name in _SUBAGENT_SIGNAL_TOOLS
        ]
        for tool in required_tools:
            if tool not in available_tools:
                available_tools.append(tool)
        # Hard enforcement: replace loaded tools so execute_msg() only sees allowed tools
        set_tools(available_tools)
    else:
        available_tools = get_tools()

    prompt_msgs = [Message("user", prompt)]

    # Build initial messages based on context_mode and context_window
    if context_window == 0:
        # Minimal context: just agent identity and tools, no workspace files.
        # This is the context isolation mode requested by the --isolate flag.
        # Note: if context_mode="selective" is also set, context_window=0 takes
        # precedence and the context_include list is ignored — agent+tools only.
        from ...prompts import prompt_gptme, prompt_tools

        if (
            context_mode == "selective"
            and context_include
            and set(context_include)
            - {
                "agent",
                "tools",
            }
        ):
            logger.warning(
                "context_window=0 overrides context_include=%r; "
                "only agent identity and tools will be included",
                context_include,
            )
        initial_msgs = list(prompt_gptme(False, None, agent_name=None)) + list(
            prompt_tools(tools=available_tools, tool_format="markdown")
        )
    elif context_mode == "selective":
        # Selective context — build from specified components.
        # context_window > 0 is not applied in selective mode; the caller
        # controls the context set explicitly via context_include instead.
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
        # Truncate workspace context if a positive window is specified.
        # Base messages (agent identity + tools) do NOT count against the
        # window — only the workspace context messages after them do.
        #
        # get_prompt() merges core sections into a single combined message
        # (via _join_messages()). In the typical subagent case (no active
        # chat history, no context_cmd) n_base=1. Dynamic sections such as
        # SYSTEM_PROMPT_CACHE_BOUNDARY, chat_history, or context_cmd output
        # may add more — the measurement below handles all cases correctly.
        if context_window is not None and context_window > 0:
            n_base = len(get_prompt(available_tools, interactive=False, workspace=None))
            initial_msgs = initial_msgs[: n_base + context_window]

    # Apply secret redaction to workspace context messages if requested.
    # This redacts values from lines where the variable name matches common
    # secret patterns (API_KEY, TOKEN, PASSWORD, etc.) in system messages.
    # Only redacts the context messages, not the task prompt itself.
    if redact_secrets:
        from .context import redact_secrets_from_messages

        initial_msgs = redact_secrets_from_messages(initial_msgs)

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

    # Inject parent conversation context if provided
    # Redact secrets from parent messages to prevent leaking API keys/tokens
    # that may appear in parent conversation output (e.g., shell output, env reads).
    if parent_messages:
        if redact_secrets:
            from .context import redact_secrets_from_messages

            parent_messages = redact_secrets_from_messages(parent_messages)
        initial_msgs.append(_build_parent_context_message(parent_messages))

    # Add completion instruction as a system message.
    # When output_schema is set, the instruction is extended with the expected
    # JSON schema so the subagent knows the format to use in its complete block.
    # output_schema is intentionally NOT passed to chat() here — the LLM-level
    # response_format (structured JSON output) conflicts with markdown tool_format
    # because the model can't write markdown code blocks while also being forced to
    # output raw JSON. Schema contract is enforced at the complete-block layer in
    # Subagent._read_log() instead.
    complete_instruction = Message(
        "system",
        _get_complete_instruction(target, output_schema=output_schema),
    )
    initial_msgs.append(complete_instruction)

    # Note: workspace parameter is always passed to chat() (required parameter)
    # Workspace context in messages is controlled by initial_msgs
    #
    # Suppress terminal output for thread-mode subagents via output_format="quiet":
    # each thread has its own ContextVar copy (Python's threading semantics), so
    # this only affects this thread and never bleeds into the parent's output
    # stream. Passing it through chat()'s own save/restore machinery (rather than
    # calling set_output_format() before the call) is required — chat() itself
    # calls set_output_format(output_format) at its start, which would otherwise
    # immediately override quiet mode back to the default "text".
    try:
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
            output_format="quiet",
        )
    finally:
        # Signal immediately when chat() returns — before any caller cleanup.
        # This closes the window between "chat() last drain" and the caller
        # acquiring _subagents_lock to find `sa`. Any concurrent subagent_steer()
        # that slips between this set and chat()'s actual last drain is a
        # sub-microsecond race that requires modifying chat() itself to close.
        if prompt_queue_closed is not None:
            prompt_queue_closed.set()
        # Drain any steer messages that arrived after the last STEP_PRE fired but
        # before chat() returned. These were not deliverable mid-turn; warn so
        # the orchestrator knows the guidance was not seen.
        try:
            stranded = drain_steer_prompts(logdir)
            if stranded:
                logger.warning(
                    "Subagent exited with %d stranded steer message(s) never "
                    "delivered (arrived after final STEP_PRE checkpoint): %s",
                    len(stranded),
                    [m.content[:60] for m in stranded],
                )
        except Exception:
            pass


def _run_subagent_subprocess(
    prompt: str,
    logdir: Path,
    model: str | None,
    workspace: Path,
    context_mode: Literal["full", "selective"] | None = None,
    context_include: list[str] | None = None,
    output_schema: str | None = None,
    output_schema_dict: dict | None = None,
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
        output_schema: ``module:ClassName`` reference for structured output (Pydantic models).
            Passed directly to the CLI's ``--output-schema`` flag.
        output_schema_dict: Pre-parsed JSON Schema dict (from plain-dict callers).
            Injected into the prompt via ``_get_complete_instruction`` rather than
            the CLI flag, because the CLI only accepts ``module:ClassName`` format.
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
        from ...profiles import get_profile

        profile_obj = get_profile(profile)
        if profile_obj and profile_obj.tools is not None:
            tool_allowlist = list(profile_obj.tools)
            for tool_name in ("complete", "clarify", "progress"):
                if tool_name not in tool_allowlist:
                    tool_allowlist.append(tool_name)
            cmd.extend(["--tools", ",".join(tool_allowlist)])
        else:
            cmd.extend(["--tools", "+complete,+clarify,+progress"])
    else:
        cmd.extend(["--tools", "+complete,+clarify,+progress"])

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

    # Progress file for subprocess-mode progress delivery.
    # The subprocess writes JSON lines here; _monitor_subprocess polls and
    # delivers them to the parent via notify_progress().
    progress_file = logdir / "progress.jsonl"

    # Add completion instruction to the prompt for subprocess mode.
    # (In thread mode, this is added as a system message.)
    # Subprocess mode supports progress via the file channel, so enable it.
    # Pass output_schema_dict (plain-dict callers) so the schema hint is
    # injected here — the CLI --output-schema flag only accepts module:ClassName.
    complete_section = (
        "\n\n[Completion Instructions]\n"
        f"{_get_complete_instruction('orchestrator', supports_progress=True, output_schema=output_schema_dict)}\n"
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

    # Environment for the subprocess: convey agent identity and progress channel.
    env = os.environ.copy()
    env["GPTME_SUBAGENT_AGENT_ID"] = logdir.name.removeprefix("subagent-")
    env["GPTME_PROGRESS_FILE"] = str(progress_file)

    try:
        with open(tmpfile_path) as stdin_file:
            process = subprocess.Popen(
                cmd,
                stdin=stdin_file,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=workspace,
                env=env,
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


def _cleanup_isolation(subagent: "Subagent") -> str | None:
    """Clean up worktree or temp directory after subagent completes.

    For ``isolation_mode="worktree"`` subagents, uses smart cleanup:
    - No local changes → remove directory AND branch (full cleanup).
    - Local changes → preserve branch, remove directory only.

    Returns:
        The preserved branch name when changes exist and smart cleanup is
        active; ``None`` otherwise.
    """
    if not subagent.isolated or not subagent.worktree_path:
        return None

    from ...util.git_worktree import cleanup_worktree

    try:
        # Smart cleanup: preserve branch when isolation="worktree" was used
        # and the worktree has local changes so the orchestrator can merge them.
        keep_if_changed = subagent.isolation_mode == "worktree"
        preserved_branch = cleanup_worktree(
            subagent.worktree_path,
            subagent.repo_path,
            keep_branch_if_changed=keep_if_changed,
        )
        if preserved_branch:
            logger.info(
                f"Subagent {subagent.agent_id!r}: changes preserved on branch "
                f"{preserved_branch!r} — merge with: git merge {preserved_branch}"
            )
        return preserved_branch
    except Exception as e:
        logger.warning(f"Failed to cleanup isolation for {subagent.agent_id}: {e}")
        return None


def _drain_progress_file(
    progress_file: "Path",
    file_pos: int,
    default_agent_id: str,
    notify_fn: "Callable[[str, str], None]",
) -> int:
    """Read new progress entries from progress_file and deliver them via notify_fn.

    Reads from ``file_pos`` forward, advancing only past complete
    newline-terminated lines. Stops (without advancing) on any incomplete line
    at EOF so the partial write can be retried on the next poll.

    Args:
        progress_file: Path to the ``progress.jsonl`` file written by the child.
        file_pos: Byte offset to start reading from.
        default_agent_id: Used when an entry omits ``agent_id``.
        notify_fn: Callable with signature ``(agent_id: str, message: str)``.

    Returns:
        Updated file_pos (advanced past every complete line that was processed).
    """
    import json

    if not progress_file.exists():
        return file_pos
    try:
        with open(progress_file) as f:
            f.seek(file_pos)
            while True:
                raw_line = f.readline()
                if not raw_line:
                    break  # EOF — no new content
                if not raw_line.endswith("\n"):
                    # Incomplete line at EOF: partial write in progress.
                    # Don't advance file_pos; retry next poll when the write completes.
                    break
                stripped = raw_line.strip()
                if not stripped:
                    file_pos = f.tell()
                    continue
                try:
                    entry = json.loads(stripped)
                    agent_id = entry.get("agent_id", default_agent_id)
                    message = entry.get("message", "")
                    if message:
                        notify_fn(agent_id, message)
                        logger.debug(
                            f"Delivered subprocess progress for '{agent_id}': {message[:80]}"
                        )
                except json.JSONDecodeError:
                    logger.debug(
                        f"Skipping unparseable progress line: {stripped[:80]!r}"
                    )
                # Advance past this complete line (parsed or corrupt) so
                # we don't re-process it on the next poll.
                file_pos = f.tell()
    except OSError:
        pass  # file not yet created or already removed
    return file_pos


def _poll_subprocess_progress(
    subagent: "Subagent", stop_event: threading.Event
) -> None:
    """Poll the progress file for a subprocess-mode subagent and deliver updates.

    Runs in a background thread alongside _monitor_subprocess. Reads JSON lines
    from ``logdir/progress.jsonl`` (written by the subprocess via the progress
    tool's file channel) and calls ``notify_progress`` so the parent receives
    ⏳ system messages via the LOOP_CONTINUE hook.

    Args:
        subagent: The subagent being monitored.
        stop_event: Set by _monitor_subprocess after the process exits. The poll
            loop runs one final drain pass after the event is set to catch any
            progress updates written in the last moments before exit.
    """
    progress_file = subagent.logdir / "progress.jsonl"
    file_pos = 0
    POLL_INTERVAL = 0.5

    def _drain() -> None:
        nonlocal file_pos
        file_pos = _drain_progress_file(
            progress_file, file_pos, subagent.agent_id, notify_progress
        )

    while not stop_event.is_set():
        _drain()
        time.sleep(POLL_INTERVAL)
    # Final drain after the process exits to catch last-moment writes.
    _drain()


def _monitor_subprocess(
    subagent: "Subagent",
) -> None:
    """Monitor a subprocess and invoke callbacks when it completes.

    Runs in a background thread to enable non-blocking operation.
    Subprocess stdout/stderr are sent to DEVNULL since results are read
    from the conversation log, not the process pipes.

    Also starts a progress-polling thread that reads ``logdir/progress.jsonl``
    written by the subprocess-mode ``progress`` tool and delivers intermediate
    updates to the parent via ``notify_progress``.
    """
    if not subagent.process:
        return

    # Start progress polling in a background thread so the parent receives
    # ⏳ updates while the subprocess is still running.
    progress_stop = threading.Event()
    progress_thread = threading.Thread(
        target=_poll_subprocess_progress,
        args=(subagent, progress_stop),
        daemon=True,
        name=f"progress-poll-{subagent.agent_id}",
    )
    progress_thread.start()

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

    # Stop the progress-poll thread and let it do a final drain.
    progress_stop.set()
    progress_thread.join(timeout=2.0)

    input_tokens: int | None = None
    output_tokens: int | None = None

    # Determine status based on return code
    if subagent.process.returncode == 0:
        status: Status = "success"
        # Get result from conversation log (primary source for subprocess mode)
        try:
            log_status = subagent.status()
            if log_status.status == "clarification_needed":
                status = "clarification_needed"
            result = log_status.result
            input_tokens = log_status.input_tokens
            output_tokens = log_status.output_tokens
        except Exception:
            result = "Task completed (check log for details)"
    elif _timed_out:
        # Process was killed because our timeout expired (not an external SIGKILL)
        status = "failure"
        result = f"Process killed after {subagent.timeout}s timeout"
    else:
        status = "failure"
        result = f"Process exited with code {subagent.process.returncode}"

    # Clean up worktree isolation; capture preserved branch so it can be
    # included in the result that callers receive via subagent_wait() / subagent_parallel().
    preserved_branch = _cleanup_isolation(subagent)
    # Skip appending human-readable branch text when output_schema is set —
    # the result string is JSON that callers parse, and appending text after
    # it would break that parse.
    if preserved_branch and isinstance(result, str) and not subagent.output_schema:
        result = (
            f"{result}\n\nChanges preserved on branch {preserved_branch!r}"
            f" — merge with: git merge {preserved_branch}"
        )

    # Cache the result in module-level dict (Subagent is frozen)
    final_result = ReturnType(
        status,
        result,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    if not set_subagent_result_if_absent(subagent.agent_id, final_result):
        # Timeout/cancel won the cache race. Patch the stored result with
        # branch info (mirrors the thread-mode fallback in api.py) so
        # callers can still find preserved work.
        if preserved_branch:
            update_subagent_result_with_branch(
                subagent.agent_id,
                preserved_branch,
                has_output_schema=bool(subagent.output_schema),
            )
        return

    # Notify via hook system (fire-and-forget-then-get-alerted pattern)
    try:
        summary = _summarize_result(final_result, max_chars=200)
        notify_completion(subagent.agent_id, status, summary)
    except Exception as e:
        logger.warning(f"Failed to notify subagent completion: {e}")


def _run_planner(
    agent_id: str,
    prompt: str,
    subtasks: "list[SubtaskDef]",
    execution_mode: Literal["parallel", "sequential"] = "parallel",
    context_mode: Literal["full", "selective"] = "full",
    context_include: list[str] | None = None,
    model: str | None = None,
    profile_name: str | None = None,
    redact_secrets: bool = True,
    context_window: int | None = None,
    workdir: Path | None = None,
    parent_logdir: Path | None = None,
) -> None:
    """Run a planner that delegates work to multiple executor subagents.

    Args:
        agent_id: Identifier for the planner
        prompt: Context prompt shared with all executors
        subtasks: List of subtask definitions to execute
        execution_mode: "parallel" (all at once) or "sequential" (one by one)
        context_mode: Controls what context is shared with executors (see subagent() docs)
        context_include: For selective mode, list of context components to include
        profile_name: Agent profile to apply to executor subagents (per-subtask role
            always overrides this when set — subtask role is more specific)
        redact_secrets: If True, scrub secret patterns from workspace context before
            thread-mode executors see it. Has no effect on subprocess-mode executors
            (which manage their own context); a debug message is logged in that case.
        context_window: Limit workspace context messages passed to executor subagents.
            None = no limit; 0 = minimal context (no workspace files); N > 0 = at most N.
        workdir: Pre-resolved working directory for all executor subagents. When
            None, each executor falls back to Path.cwd() at spawn time.
    """
    from gptme.cli.main import get_logdir

    from .types import (
        Subagent,
        _subagent_results,
        _subagent_results_lock,
        _subagents,
        _subagents_lock,
        resolve_role_defaults,
    )

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

        # Resolve role-based defaults per subtask if role is set.
        # Per-subtask role is more specific than the planner-level profile, so it
        # always wins — even when the planner itself carries a profile.
        # Role also controls execution mode: role="verify" enables subprocess+isolated
        # for sandboxed validation; role="explore"/"implement" use thread mode (default).
        subtask_role = subtask.get("role")
        resolved_profile = profile_name
        resolved_use_subprocess = False
        resolved_isolated = False
        if subtask_role:
            role_use_sub, role_isolated, role_profile = resolve_role_defaults(
                subtask_role, None, None
            )
            if role_profile is not None:
                resolved_profile = role_profile
            resolved_use_subprocess = role_use_sub
            resolved_isolated = role_isolated
            logger.info(
                f"Subtask '{subtask['id']}' resolved from role='{subtask_role}': "
                f"profile={resolved_profile!r}, "
                f"use_subprocess={resolved_use_subprocess}, "
                f"isolated={resolved_isolated}"
            )

        # Explicit workdir > current working directory. Capture before spawning
        # to avoid FileNotFoundError if cwd is deleted (e.g., tmpdir cleanup in tests)
        if workdir is not None:
            workspace = workdir
        else:
            try:
                workspace = Path.cwd()
            except FileNotFoundError:
                workspace = logdir.parent

        # Set up worktree isolation if the resolved role requires it
        worktree_path: Path | None = None
        repo_path: Path | None = None
        if resolved_isolated:
            from ...util.git_worktree import create_worktree, get_git_root

            git_root = get_git_root(workspace)
            if git_root:
                try:
                    branch_name = f"subagent-{executor_id}-{uuid.uuid4().hex[:8]}"
                    worktree_path = create_worktree(git_root, branch_name=branch_name)
                    workspace = worktree_path
                    repo_path = git_root
                    logger.info(
                        f"Executor {executor_id} isolated in worktree: {worktree_path}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to create worktree for {executor_id}, "
                        f"falling back to temp dir: {e}"
                    )
                    worktree_path = Path(
                        tempfile.mkdtemp(prefix=f"subagent-{executor_id}-")
                    )
                    workspace = worktree_path
            else:
                worktree_path = Path(
                    tempfile.mkdtemp(prefix=f"subagent-{executor_id}-")
                )
                workspace = worktree_path
                logger.info(
                    f"Not in a git repo, using temp dir for {executor_id}: {worktree_path}"
                )

        if resolved_use_subprocess:
            if redact_secrets:
                logger.debug(
                    f"Planner executor {executor_id}: 'redact_secrets=True' has no effect "
                    "in subprocess mode (only thread-mode subagents inherit workspace context)"
                )
            sa = Subagent(
                executor_id,
                executor_prompt,
                None,
                logdir,
                model,
                context_mode=context_mode,
                context_include=context_include,
                profile=resolved_profile,
                process=None,
                execution_mode="subprocess",
                isolated=resolved_isolated,
                worktree_path=worktree_path,
                repo_path=repo_path,
                role=subtask_role,
                parent_logdir=parent_logdir,
            )

            # Subprocess mode: a combined thread acquires the concurrency slot before
            # Popen, monitors to completion, and releases in finally — same pattern as
            # api.py _launch_subprocess. Captures all loop vars via default args.
            def _run_executor_subprocess(
                _sa: "Subagent" = sa,
                _workspace=workspace,
                _profile=resolved_profile,
            ):
                # Bind retry generation at thread birth so test-teardown
                # interrupts abort backoffs even post-teardown (see retry_abort).
                bind_thread_generation()
                _sem = get_slot_sem()
                _sem.acquire()
                try:
                    with _subagent_results_lock:
                        if _sa.agent_id in _subagent_results:
                            logger.info(
                                "Skipping cancelled queued planner subprocess "
                                f"executor {_sa.agent_id}"
                            )
                            _cleanup_isolation(_sa)
                            return
                    try:
                        process = _run_subagent_subprocess(
                            prompt=_sa.prompt,
                            logdir=_sa.logdir,
                            model=_sa.model,
                            workspace=_workspace,
                            context_mode=context_mode,
                            context_include=context_include,
                            profile=_profile,
                        )
                    except Exception as e:
                        logger.error(
                            f"Executor {_sa.agent_id} subprocess failed: {e}",
                            exc_info=True,
                        )
                        from .hooks import notify_completion
                        from .types import ReturnType, set_subagent_result_if_absent

                        if set_subagent_result_if_absent(
                            _sa.agent_id, ReturnType("failure", str(e))
                        ):
                            notify_completion(
                                _sa.agent_id,
                                "failure",
                                f"Executor subprocess failed: {e}",
                            )
                        _cleanup_isolation(_sa)
                        return
                    object.__setattr__(_sa, "process", process)
                    _monitor_subprocess(_sa)
                finally:
                    release_thread()
                    _sem.release()

            monitor_t = threading.Thread(target=_run_executor_subprocess, daemon=True)
            object.__setattr__(sa, "thread", monitor_t)
            with _subagents_lock:
                _subagents.append(sa)
            monitor_t.start()

            # Sequential mode: wait for this executor before starting the next
            if execution_mode == "sequential":
                logger.info(
                    f"Waiting for {executor_id} subprocess to complete (sequential mode)"
                )
                monitor_t.join()
                logger.info(f"Executor {executor_id} subprocess completed")
        else:
            # Thread mode: original behavior for explore/implement roles
            cleanup_sa = Subagent(
                executor_id,
                executor_prompt,
                None,
                logdir,
                model,
                isolated=resolved_isolated,
                worktree_path=worktree_path,
                repo_path=repo_path,
                role=subtask_role,
                parent_logdir=parent_logdir,
            )

            def run_executor(
                executor_agent_id=executor_id,
                prompt=executor_prompt,
                log_dir=logdir,
                ws=workspace,
                subtask_profile=resolved_profile,
                cleanup_subagent=cleanup_sa,
            ):
                # Bind retry generation at thread birth so test-teardown
                # interrupts abort backoffs even post-teardown (see retry_abort).
                bind_thread_generation()
                _sem = get_slot_sem()
                _sem.acquire()
                try:
                    with _subagent_results_lock:
                        if executor_agent_id in _subagent_results:
                            logger.info(
                                "Skipping cancelled queued planner thread "
                                f"executor {executor_agent_id}"
                            )
                            return
                    _create_subagent_thread(
                        prompt=prompt,
                        logdir=log_dir,
                        model=model,
                        context_mode=context_mode,
                        context_include=context_include,
                        workspace=ws,
                        target="planner",
                        profile_name=subtask_profile,
                        agent_id=executor_agent_id,
                        redact_secrets=redact_secrets,
                        context_window=context_window,
                    )
                finally:
                    release_thread()
                    try:
                        _cleanup_isolation(cleanup_subagent)
                    except Exception:
                        logger.exception(
                            "Failed to clean up isolated planner thread executor "
                            f"{executor_agent_id}"
                        )
                    finally:
                        _sem.release()

            t = threading.Thread(target=run_executor, daemon=True)
            sa = Subagent(
                executor_id,
                executor_prompt,
                t,
                logdir,
                model,
                context_mode=context_mode,
                context_include=context_include,
                profile=resolved_profile,
                isolated=resolved_isolated,
                worktree_path=worktree_path,
                repo_path=repo_path,
                role=subtask_role,
                parent_logdir=parent_logdir,
            )
            # Register subagent BEFORE starting thread to avoid race condition
            # (matches pattern in api.py — thread closure may look up _subagents)
            with _subagents_lock:
                _subagents.append(sa)
            t.start()

            # Sequential mode: wait for each task to complete before starting next
            if execution_mode == "sequential":
                logger.info(f"Waiting for {executor_id} to complete (sequential mode)")
                t.join()
                logger.info(f"Executor {executor_id} completed")

    # Parallel mode: all executors (threads/subprocesses) already started
    if execution_mode == "parallel":
        logger.info(f"Planner {agent_id} spawned {len(subtasks)} executor subagents")
    else:
        logger.info(
            f"Planner {agent_id} completed {len(subtasks)} subtasks sequentially"
        )
