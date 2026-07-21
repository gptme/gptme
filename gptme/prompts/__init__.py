"""
This module contains the functions to generate the initial system prompt.
It is used to instruct the LLM about its role, how to use tools, and provide context for the conversation.

When prompting, it is important to provide clear instructions and avoid any ambiguity.
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass
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

PromptType = Literal["full", "short", "full-noexamples"]

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_CACHE_BOUNDARY = """# System Prompt Cache Boundary

Static bootstrap content ends above. Session-volatile context starts below.

---"""


ContextMode = Literal["full", "selective"]


@dataclass(frozen=True)
class PromptSectionStat:
    name: str
    messages: int
    chars: int
    tokens: int


@dataclass(frozen=True)
class PromptStats:
    sections: tuple[PromptSectionStat, ...]
    total_messages: int
    total_chars: int
    total_tokens: int
    cacheable_tokens: int
    dynamic_tokens: int


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


def _build_core_prompt_sections(
    *,
    prompt: PromptType | str,
    interactive: bool,
    model: str | None,
    agent_name: str | None,
    tool_format: ToolFormat,
    tools: list[ToolSpec],
    workspace: Path | None,
    include_tools: bool,
    include_examples: bool,
    is_selective: bool,
) -> list[tuple[str, list[Message]]]:
    """Build named core prompt sections in display order."""
    sections: list[tuple[str, list[Message]]] = []

    def add(name: str, msgs: list[Message]) -> None:
        if msgs:
            sections.append((name, msgs))

    if is_selective and not include_tools:
        add(
            "prompt_gptme",
            list(prompt_gptme(interactive, model, agent_name, tool_format=tool_format)),
        )
        return sections

    if prompt == "full":
        add(
            "prompt_gptme",
            list(prompt_gptme(interactive, model, agent_name, tool_format=tool_format)),
        )
        if include_tools:
            add(
                "prompt_tools",
                list(
                    prompt_tools(
                        tools=tools,
                        tool_format=tool_format,
                        model=model,
                        examples=include_examples,
                    )
                ),
            )
        if interactive:
            add("prompt_user", list(prompt_user(tool_format=tool_format)))
        add("prompt_project", list(prompt_project(tool_format=tool_format)))
        add(
            "prompt_systeminfo",
            list(prompt_systeminfo(workspace, tool_format=tool_format)),
        )
        add("prompt_timeinfo", list(prompt_timeinfo(tool_format=tool_format)))
        if include_tools:
            add(
                "prompt_skills_summary",
                list(prompt_skills_summary(tool_format=tool_format)),
            )
        return sections

    if prompt == "short":
        add(
            "prompt_gptme",
            list(
                prompt_gptme(
                    interactive,
                    model,
                    agent_name=agent_name,
                    tool_format=tool_format,
                    compact=True,
                )
            ),
        )
        if include_tools:
            add(
                "prompt_tools",
                list(
                    prompt_tools(
                        examples=False,
                        tools=tools,
                        tool_format=tool_format,
                        model=model,
                    )
                ),
            )
            if interactive:
                add("prompt_user", list(prompt_user(tool_format=tool_format)))
            add("prompt_project", list(prompt_project(tool_format=tool_format)))
        return sections

    add("custom_prompt", [Message("system", prompt)])
    if include_tools:
        add(
            "prompt_tools",
            list(
                prompt_tools(
                    tools=tools,
                    tool_format=tool_format,
                    model=model,
                    examples=include_examples,
                )
            ),
        )
    return sections


def _build_prompt_sections(
    *,
    tools: list[ToolSpec],
    tool_format: ToolFormat,
    prompt: PromptType | str,
    interactive: bool,
    model: str | None,
    workspace: Path | None,
    agent_path: Path | None,
    context_mode: ContextMode | None,
    context_include: list[str] | None,
    include_user_context: bool,
    include_examples: bool,
    initial_prompt: str | None,
) -> tuple[
    list[tuple[str, list[Message]]],
    list[tuple[str, list[Message]]],
    list[tuple[str, list[Message]]],
]:
    """Build named prompt sections for core, cacheable workspace, and dynamic parts."""
    agent_config = get_project_config(agent_path)
    agent_name = (
        agent_config.agent.name if agent_config and agent_config.agent else None
    )

    effective_mode = context_mode or "full"
    include_set = set(context_include or [])
    if "all" in include_set:
        include_set.update(("files", "cmd"))
    include_set.discard("agent")
    include_set.discard("agent-config")
    is_selective = effective_mode == "selective"
    include_tools = bool(tools)
    include_workspace = effective_mode == "full" or (
        is_selective and "files" in include_set
    )
    include_agent_config = bool(agent_path)
    include_context_cmd = effective_mode == "full" or (
        is_selective and "cmd" in include_set
    )

    core_sections = _build_core_prompt_sections(
        prompt=prompt,
        interactive=interactive,
        model=model,
        agent_name=agent_name,
        tool_format=tool_format,
        tools=tools,
        workspace=workspace,
        include_tools=include_tools,
        include_examples=include_examples,
        is_selective=is_selective,
    )

    cacheable_sections: list[tuple[str, list[Message]]] = []
    if include_agent_config:
        cacheable_sections.append(
            (
                "prompt_agent_workspace",
                list(
                    prompt_workspace(
                        agent_path,
                        title="Agent Config",
                        include_path=True,
                        include_context_cmd=False,
                        include_user_context=include_user_context,
                    )
                ),
            )
        )
    if include_workspace and workspace and workspace != agent_path:
        cacheable_sections.append(
            (
                "prompt_workspace",
                list(
                    prompt_workspace(
                        workspace,
                        include_context_cmd=False,
                        include_user_context=include_user_context,
                    )
                ),
            )
        )

    dynamic_sections: list[tuple[str, list[Message]]] = []
    if include_context_cmd:
        for ws, title, section_name in [
            (
                agent_path if include_agent_config else None,
                "Agent",
                "prompt_context_cmd_agent",
            ),
            (
                workspace if include_workspace and workspace != agent_path else None,
                "Project",
                "prompt_context_cmd_project",
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
                        ws_project.context_cmd, ws, initial_prompt=initial_prompt
                    )
                )
            ):
                dynamic_sections.append(
                    (
                        section_name,
                        [
                            Message(
                                "system",
                                f"## {title} computed context\n\n" + cmd_output,
                            )
                        ],
                    )
                )

    chat_history_msgs = list(prompt_chat_history())
    if chat_history_msgs:
        dynamic_sections.append(("prompt_chat_history", chat_history_msgs))

    return core_sections, cacheable_sections, dynamic_sections


def _section_stat(
    name: str, msgs: list[Message], model: str | None
) -> PromptSectionStat:
    token_model = model or "gpt-4"
    return PromptSectionStat(
        name=name,
        messages=len(msgs),
        chars=sum(len(msg.content) for msg in msgs),
        tokens=len_tokens(msgs, token_model),
    )


def get_prompt_stats(
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
    include_examples: bool = True,
    initial_prompt: str | None = None,
) -> PromptStats:
    """Return token statistics for each startup prompt section."""
    if prompt == "full-noexamples":
        prompt = "full"
        include_examples = False
    core_sections, cacheable_sections, dynamic_sections = _build_prompt_sections(
        tools=tools,
        tool_format=tool_format,
        prompt=prompt,
        interactive=interactive,
        model=model,
        workspace=workspace,
        agent_path=agent_path,
        context_mode=context_mode,
        context_include=context_include,
        include_user_context=include_user_context,
        include_examples=include_examples,
        initial_prompt=initial_prompt,
    )

    stats = tuple(
        _section_stat(name, msgs, model)
        for name, msgs in (*core_sections, *cacheable_sections, *dynamic_sections)
    )
    cacheable_section_count = len(core_sections) + len(cacheable_sections)
    total_messages = sum(stat.messages for stat in stats)
    total_chars = sum(stat.chars for stat in stats)
    total_tokens = sum(stat.tokens for stat in stats)
    cacheable_tokens = sum(stat.tokens for stat in stats[:cacheable_section_count])
    dynamic_tokens = sum(stat.tokens for stat in stats[cacheable_section_count:])
    return PromptStats(
        sections=stats,
        total_messages=total_messages,
        total_chars=total_chars,
        total_tokens=total_tokens,
        cacheable_tokens=cacheable_tokens,
        dynamic_tokens=dynamic_tokens,
    )


def format_prompt_stats(
    stats: PromptStats,
    *,
    header: str | None = None,
    extra_sections: list[PromptSectionStat] | None = None,
) -> str:
    """Format prompt stats as a compact plain-text table."""
    sections = list(stats.sections)
    if extra_sections:
        sections.extend(extra_sections)

    total_messages = sum(section.messages for section in sections)
    total_chars = sum(section.chars for section in sections)
    total_tokens = sum(section.tokens for section in sections)
    cacheable_tokens = stats.cacheable_tokens + sum(
        section.tokens for section in (extra_sections or [])
    )

    name_width = max(
        len("section"), *(len(section.name) for section in sections), len("total")
    )
    lines = []
    if header:
        lines.append(header)
    lines.append(f"{'section':<{name_width}}  {'msgs':>4}  {'chars':>8}  {'tokens':>8}")
    lines.extend(
        [
            f"{section.name:<{name_width}}  {section.messages:>4}  {section.chars:>8}  {section.tokens:>8}"
            for section in sections
        ]
    )
    lines.append(
        f"{'total':<{name_width}}  {total_messages:>4}  {total_chars:>8}  {total_tokens:>8}"
    )
    lines.append(f"cacheable_tokens: {cacheable_tokens}")
    lines.append(f"dynamic_tokens:   {total_tokens - cacheable_tokens}")
    return "\n".join(lines)


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
    include_examples: bool = True,
    initial_prompt: str | None = None,
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
        include_examples: Whether to include tool usage examples in the system
            prompt. Defaults to True. Also set to False automatically when
            ``prompt == "full-noexamples"``.
        initial_prompt: First user prompt, exported to context commands as
            ``GPTME_PROMPT_INITIAL``.

    Returns a list of messages: [core_system_prompt, workspace_prompt, ...].
    """
    if prompt == "full-noexamples":
        prompt = "full"
        include_examples = False
    core_sections, cacheable_sections, dynamic_sections = _build_prompt_sections(
        tools=tools,
        tool_format=tool_format,
        prompt=prompt,
        interactive=interactive,
        model=model,
        workspace=workspace,
        agent_path=agent_path,
        context_mode=context_mode,
        context_include=context_include,
        include_user_context=include_user_context,
        include_examples=include_examples,
        initial_prompt=initial_prompt,
    )

    core_msgs = [msg for _, msgs in core_sections for msg in msgs]
    result = []
    if core_msgs:
        core_prompt = _join_messages(core_msgs)
        result.append(core_prompt)

    for _, msgs in cacheable_sections:
        result.extend(msgs)

    # Insert an explicit static/dynamic boundary before context_cmd output.
    # This keeps the prompt structure stable and makes the cacheable prefix
    # visible to both humans and providers with block-level prompt caching.
    if dynamic_sections and result:
        result.append(Message("system", SYSTEM_PROMPT_CACHE_BOUNDARY))

    # Dynamic context last (changes every session, least cacheable)
    for _, msgs in dynamic_sections:
        result.extend(msgs)

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
    "SYSTEM_PROMPT_CACHE_BOUNDARY",
    "ContextMode",
    "DEFAULT_CONTEXT_FILES",
    "PromptType",
    "PromptSectionStat",
    "PromptStats",
    "_loaded_agent_files_var",
    "_truncate_context_output",
    "_xml_section",
    "find_agent_files_in_tree",
    "format_prompt_stats",
    "get_project_context_cmd_output",
    "get_prompt",
    "get_prompt_stats",
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
