"""
This module contains the functions to generate the initial system prompt.
It is used to instruct the LLM about its role, how to use tools, and provide context for the conversation.

When prompting, it is important to provide clear instructions and avoid any ambiguity.
"""

import logging
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from ..config import get_project_config
from ..llm.models import get_recommended_model
from ..message import Message
from ..tools import ToolFormat, ToolSpec, get_available_tools
from ..util import document_prompt_function
from ..util.tokens import len_tokens

# Agent instruction files — always loaded (layered: user-level + project-level)
# These are the standard filenames used across different AI coding tools.
# Cross-tool compatibility: we load instruction files from multiple AI coding tools
# so that projects using any tool's convention get their rules respected by gptme.
AGENT_FILES = [
    "AGENTS.md",
    "CLAUDE.md",  # Claude Code
    "COPILOT.md",  # gptme-invented convention mirroring CLAUDE.md/GEMINI.md
    "GEMINI.md",  # Gemini
    ".github/copilot-instructions.md",  # GitHub Copilot official project instructions
    ".cursorrules",  # Cursor legacy project rules
    ".windsurfrules",  # Windsurf/Codeium project rules
]
# Keep old name for backwards compatibility with any external code (now includes cross-tool files)
ALWAYS_LOAD_FILES = AGENT_FILES

# ContextVar tracking which agent instruction files have been loaded into the session.
# Populated by prompt_workspace() at startup; used by the agents_md_inject hook
# (gptme/hooks/agents_md_inject.py) to avoid re-injecting files on CWD changes.
_loaded_agent_files_var: ContextVar[set[str] | None] = ContextVar(
    "loaded_agent_files", default=None
)

# Default files to include in context when no gptme.toml is present or files list is empty
# These are project-specific files that provide useful context
DEFAULT_CONTEXT_FILES = [
    "README*",
    ".cursor/rules/*.mdc",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "Makefile",
    "docker-compose.y*ml",
]

PromptType = Literal["full", "short"]

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_CACHE_BOUNDARY = """# System Prompt Cache Boundary

Static bootstrap content ends above. Session-volatile context starts below.

---"""


ContextMode = Literal["full", "selective"]


def _join_messages(msgs: list[Message]) -> Message:
    """Combine several system prompt messages into one."""
    role = msgs[0].role if msgs else "system"
    assert all(m.role == role for m in msgs), "All messages must be of same role"
    return Message(
        role,
        "\n\n".join(m.content for m in msgs),
        hide=any(m.hide for m in msgs),
        pinned=any(m.pinned for m in msgs),
    )


def _xml_section(tag: str, content: str) -> str:
    """Wrap content in an XML section tag.

    The content is NOT escaped — it may contain nested XML tags (e.g. tools).
    Only use xml_escape() on leaf text that should not contain markup.
    """
    return f"<{tag}>\n{content.strip()}\n</{tag}>"


# Sub-module imports for orchestration
# NOTE: these must come AFTER _xml_section and constants are defined,
# since sub-modules import from this __init__.
from .chat_history import prompt_chat_history, use_chat_history_context
from .context_cmd import (
    CONTEXT_CMD_MAX_CHARS,
    _truncate_context_output,
    get_project_context_cmd_output,
)
from .skills import prompt_skills_summary
from .templates import (
    prompt_full,
    prompt_gptme,
    prompt_project,
    prompt_short,
    prompt_systeminfo,
    prompt_timeinfo,
    prompt_tools,
    prompt_user,
)
from .workspace import find_agent_files_in_tree, prompt_workspace


def get_prompt(
    tools: list[ToolSpec],
    tool_format: ToolFormat = "markdown",
    prompt: PromptType | str = "full",
    interactive: bool = True,
    model: str | None = None,
    workspace: Path | None = None,
    agent_path: Path | None = None,
    context_mode: ContextMode | None = None,
    context_include: list[str] | None = None,
    include_user_context: bool = True,
    profile_prompt: Message | None = None,
    show_prompt_stats: bool = False,
) -> list[Message]:
    """
    Get the initial system prompt.

    The prompt is assembled from several layers:

    1. **Core prompt** (always included):

       - Base gptme identity and instructions
       - User identity/preferences (interactive only, from user config ``[user]``;
         skipped in ``--non-interactive`` since no human is present)
       - Tool descriptions (when tools are loaded, controlled by ``--tools``)

    2. **Context** (controlled by ``--context``, independent of ``--non-interactive``):

       - ``files``: static files from project config (gptme.toml ``[prompt] files``)
         and user config (``~/.config/gptme/config.toml`` ``[prompt] files``).
         Both sources are merged and deduplicated.
       - ``cmd``: dynamic output of ``context_cmd`` in gptme.toml (project-level only,
         no user-level equivalent). Changes most often, least cacheable.

    3. **Agent config** (implicit when ``--agent-path`` is provided):

       - Separate agent identity workspace. If ``agent_path == workspace``,
         workspace is skipped to avoid duplication.

    ``--context`` selects which context components to include.
    Without it, all context is included (full mode).

    Implicit behavior (not controlled by ``--context``):

    - **Tool descriptions** are always included when tools are loaded
    - **Agent config** is always loaded when ``--agent-path`` is specified

    Args:
        tools: List of available tools
        tool_format: Format for tool descriptions
        prompt: Prompt type or custom prompt string
        interactive: Whether in interactive mode
        model: Model to use
        workspace: Project workspace directory
        agent_path: Agent identity workspace (if different from project workspace)
        context_mode: Context mode (full or selective)
        context_include: Components to include in selective mode
        include_user_context: Whether to include user-level prompt files and
            agent instruction files from ~/.config/gptme
        profile_prompt: Optional profile system prompt appended after chat history

    Returns a list of messages: [core_system_prompt, workspace_prompt, ...].

    If ``show_prompt_stats`` is True, per-section token counts are logged at
    INFO level after assembly. Uses the configured model for accurate counting
    when tiktoken is available, falling back to character-based approximation.
    """
    agent_config = get_project_config(agent_path)
    agent_name = (
        agent_config.agent.name if agent_config and agent_config.agent else None
    )

    # Default context_mode to "full" if not specified
    effective_mode = context_mode or "full"
    include_set = set(context_include or [])

    # Determine what to include based on context_mode
    # Expand aliases
    if "all" in include_set:
        include_set.update(("files", "cmd"))
    # Legacy: "agent" in context_include is ignored (agent-path is now always loaded)
    include_set.discard("agent")
    include_set.discard("agent-config")
    is_selective = effective_mode == "selective"
    # Tools are always included when they're loaded — no need to opt-in via --context-include
    include_tools = bool(tools)
    include_workspace = effective_mode == "full" or (
        is_selective and "files" in include_set
    )
    # Agent workspace is always loaded when --agent-path is provided
    include_agent_config = bool(agent_path)
    include_context_cmd = effective_mode == "full" or (
        is_selective and "cmd" in include_set
    )

    # Generate core system messages (without workspace context)
    core_msgs: list[Message]
    if is_selective and not include_tools:
        # Selective mode with no tools loaded: base prompt only
        core_msgs = list(
            prompt_gptme(interactive, model, agent_name, tool_format=tool_format)
        )
    elif prompt == "full":
        if include_tools:
            core_msgs = list(
                prompt_full(
                    interactive,
                    tools,
                    tool_format,
                    model,
                    agent_name=agent_name,
                    workspace=workspace,
                )
            )
        else:
            # Full mode without tools
            # Note: skills summary is intentionally excluded here since skills
            # require tool access (e.g., `cat <path>`) to load on-demand
            core_msgs = list(
                prompt_gptme(interactive, model, agent_name, tool_format=tool_format)
            )
            if interactive:
                core_msgs.extend(prompt_user(tool_format=tool_format))
            core_msgs.extend(prompt_project(tool_format=tool_format))
            core_msgs.extend(prompt_systeminfo(workspace, tool_format=tool_format))
            core_msgs.extend(prompt_timeinfo(tool_format=tool_format))
    elif prompt == "short":
        if include_tools:
            core_msgs = list(
                prompt_short(
                    interactive,
                    tools,
                    tool_format,
                    model=model,
                    agent_name=agent_name,
                )
            )
        else:
            core_msgs = list(
                prompt_gptme(interactive, model, agent_name, tool_format=tool_format)
            )
    else:
        core_msgs = [Message("system", prompt)]
        if tools and include_tools:
            core_msgs.extend(
                prompt_tools(tools=tools, tool_format=tool_format, model=model)
            )

    # Generate workspace messages separately (if included)
    # Always exclude context_cmd here — it's collected separately below
    # for better prompt caching (static/semi-static content first, dynamic last).
    workspace_msgs = (
        list(
            prompt_workspace(
                workspace,
                include_context_cmd=False,
                include_user_context=include_user_context,
            )
        )
        if include_workspace and workspace and workspace != agent_path
        else []
    )

    # Agent config workspace (separate from project, only with --agent-path)
    agent_config_msgs = (
        list(
            prompt_workspace(
                agent_path,
                title="Agent Config",
                include_path=True,
                include_context_cmd=False,
                include_user_context=include_user_context,
            )
        )
        if include_agent_config
        else []
    )

    # Collect dynamic context_cmd outputs separately.
    # By placing these after all static/semi-static content, we maximize the
    # prompt prefix that can be cached across conversation starts (core prompt,
    # workspace files, agent config rarely change; context_cmd changes every session).
    dynamic_context_msgs: list[Message] = []
    if include_context_cmd:
        for ws, title in [
            (agent_path if include_agent_config else None, "Agent"),
            (
                workspace if include_workspace and workspace != agent_path else None,
                "Project",
            ),
        ]:
            if ws is None:
                continue
            ws_project = get_project_config(ws)
            if (
                ws_project
                and ws_project.context_cmd
                and (
                    cmd_output := get_project_context_cmd_output(
                        ws_project.context_cmd, ws
                    )
                )
            ):
                dynamic_context_msgs.append(
                    Message("system", f"## {title} computed context\n\n" + cmd_output)
                )

    # Combine core messages into one system prompt
    result = []
    core_prompt: Message | None = None
    if core_msgs:
        core_prompt = _join_messages(core_msgs)
        result.append(core_prompt)

    # Add agent config messages separately (if included)
    if include_agent_config:
        result.extend(agent_config_msgs)

    # Add workspace messages separately (if included)
    if include_workspace:
        result.extend(workspace_msgs)

    # Insert an explicit static/dynamic boundary before context_cmd output.
    # This keeps the prompt structure stable and makes the cacheable prefix
    # visible to both humans and providers with block-level prompt caching.
    boundary_added = False
    if dynamic_context_msgs and result:
        result.append(Message("system", SYSTEM_PROMPT_CACHE_BOUNDARY))
        boundary_added = True

    # Dynamic context last (changes every session, least cacheable)
    result.extend(dynamic_context_msgs)

    # Chat history (also dynamic)
    chat_history_msgs = list(prompt_chat_history())
    result.extend(chat_history_msgs)

    if profile_prompt is not None:
        result.append(profile_prompt)

    # Set hide=True, pinned=True for all messages
    for i, msg in enumerate(result):
        result[i] = msg.replace(hide=True, pinned=True)

    # Per-section token stats run after every prompt section is populated.
    if show_prompt_stats:
        effective_model = model or get_recommended_model("openai")
        _log_prompt_stats(
            core_prompt=core_prompt,
            agent_config_msgs=agent_config_msgs,
            workspace_msgs=workspace_msgs,
            dynamic_context_msgs=dynamic_context_msgs,
            chat_history_msgs=chat_history_msgs,
            profile_prompt=profile_prompt,
            static_dynamic_boundary=boundary_added,
            model=effective_model,
        )

    return result


def _log_prompt_stats(
    *,
    core_prompt: Message | None,
    agent_config_msgs: list[Message],
    workspace_msgs: list[Message],
    dynamic_context_msgs: list[Message],
    chat_history_msgs: list[Message],
    profile_prompt: Message | None,
    static_dynamic_boundary: bool,
    model: str,
) -> None:
    """Log per-section token counts for the assembled system prompt."""
    sections: list[tuple[str, list[Message]]] = []
    if core_prompt is not None:
        sections.append(("Core (identity + tools)", [core_prompt]))
    if agent_config_msgs:
        sections.append(("Agent config", agent_config_msgs))
    if workspace_msgs:
        sections.append(("Workspace files", workspace_msgs))
    if static_dynamic_boundary:
        # Boundary message itself is a single short Message in result
        sections.append(
            (
                "Static/dynamic boundary",
                [Message("system", SYSTEM_PROMPT_CACHE_BOUNDARY)],
            )
        )
    if dynamic_context_msgs:
        sections.append(("Dynamic context (context_cmd)", dynamic_context_msgs))
    if chat_history_msgs:
        sections.append(("Chat history", chat_history_msgs))
    if profile_prompt is not None:
        sections.append(("Agent profile", [profile_prompt]))

    # Count per section
    rows: list[tuple[str, int, float]] = []
    total = 0
    for name, msgs in sections:
        tokens = len_tokens(msgs, model) if msgs else 0
        total += tokens
        rows.append((name, tokens, 0.0))

    if not rows:
        logger.info("No prompt sections to report.")
        return

    # Compute percentages
    if total > 0:
        rows = [(name, tokens, tokens / total * 100) for name, tokens, _pct in rows]

    # Build table
    lines = ["", "=== System prompt token breakdown ==="]
    col_name = max(len(r[0]) for r in rows) + 2
    lines.append(f"  {'Section':<{col_name}} {'Tokens':>8}  {'Pct':>6}")
    lines.append(f"  {'-' * col_name} {'-' * 8}  {'-' * 6}")
    for name, tokens, pct in rows:
        lines.append(f"  {name:<{col_name}} {tokens:>8}  {pct:>5.1f}%")
    lines.append(f"  {'-' * col_name} {'-' * 8}  {'-' * 6}")
    lines.append(f"  {'Total':<{col_name}} {total:>8}")
    lines.append("=====================================")

    for line in lines:
        logger.info(line)


document_prompt_function(
    interactive=True,
    model=get_recommended_model("anthropic"),
)(prompt_gptme)
document_prompt_function()(prompt_user)
document_prompt_function()(prompt_project)
document_prompt_function(tools=lambda: get_available_tools(), tool_format="markdown")(
    prompt_tools
)
# document_prompt_function(tool_format="xml")(prompt_tools)
# document_prompt_function(tool_format="tool")(prompt_tools)
document_prompt_function()(prompt_systeminfo)
document_prompt_function()(prompt_chat_history)


# Public API re-exports
__all__ = [
    "AGENT_FILES",
    "ALWAYS_LOAD_FILES",
    "CONTEXT_CMD_MAX_CHARS",
    "SYSTEM_PROMPT_CACHE_BOUNDARY",
    "ContextMode",
    "DEFAULT_CONTEXT_FILES",
    "PromptType",
    "_loaded_agent_files_var",
    "_truncate_context_output",
    "_xml_section",
    "find_agent_files_in_tree",
    "get_project_context_cmd_output",
    "get_prompt",
    "prompt_chat_history",
    "prompt_full",
    "prompt_gptme",
    "prompt_project",
    "prompt_short",
    "prompt_skills_summary",
    "prompt_systeminfo",
    "prompt_timeinfo",
    "prompt_tools",
    "prompt_user",
    "prompt_workspace",
    "use_chat_history_context",
]
