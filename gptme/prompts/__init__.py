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

# Agent instruction files — always loaded (layered: user-level + project-level)
# These are the standard filenames used across different AI coding tools.
AGENT_FILES = [
    "AGENTS.md",
    "CLAUDE.md",  # Claude Code compatibility
    "GEMINI.md",  # Gemini compatibility
]
# Keep old name for backwards compatibility with any external code
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

    Returns a list of messages: [core_system_prompt, workspace_prompt, ...].
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
                prompt_short(interactive, tools, tool_format, agent_name=agent_name)
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

    # TODO: generate context_cmd outputs separately and put them last in a "dynamic context" section
    #       with context known not to cache well across conversation starts, so that cache points can be set before and better utilized/changed less frequently.
    #       probably together with chat history since it's also dynamic/live context.
    #       as opposed to static (core/system prompt) and semi-static (workspace/project prompt, like files).

    # Generate workspace messages separately (if included)
    workspace_msgs = (
        list(prompt_workspace(workspace, include_context_cmd=include_context_cmd))
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
                include_context_cmd=include_context_cmd,
            )
        )
        if include_agent_config
        else []
    )

    # Combine core messages into one system prompt
    result = []
    if core_msgs:
        core_prompt = _join_messages(core_msgs)
        result.append(core_prompt)

    # Add agent config messages separately (if included)
    if include_agent_config:
        result.extend(agent_config_msgs)

    # Add workspace messages separately (if included)
    if include_workspace:
        result.extend(workspace_msgs)

    # Generate cross-conversation context if enabled
    # Add chat history context
    result.extend(prompt_chat_history())

    # Set hide=True, pinned=True for all messages
    for i, msg in enumerate(result):
        result[i] = msg.replace(hide=True, pinned=True)

    return result


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
