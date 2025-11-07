"""
This module contains the functions to generate the initial system prompt.
It is used to instruct the LLM about its role, how to use tools, and provide context for the conversation.

When prompting, it is important to provide clear instructions and avoid any ambiguity.
"""

import logging
import platform
import subprocess
import time
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .__version__ import __version__
from .config import config_path, get_config, get_project_config
from .dirs import get_project_git_dir
from .llm.models import get_model, get_recommended_model
from .message import Message, len_tokens
from .tools import ToolFormat, ToolSpec, get_available_tools
from .util import document_prompt_function
from .util.content import extract_content_summary
from .util.context import md_codeblock
from .util.tree import get_tree_output

# Default files to include in context when no gptme.toml is present or files list is empty
DEFAULT_CONTEXT_FILES = [
    "README*",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".cursor/rules/*.mdc",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "Makefile",
    "docker-compose.y*ml",
]

PromptType = Literal["full", "short"]

logger = logging.getLogger(__name__)


def get_prompt(
    tools: list[ToolSpec],
    tool_format: ToolFormat = "markdown",
    prompt: PromptType | str = "full",
    interactive: bool = True,
    model: str | None = None,
    workspace: Path | None = None,
    agent_path: Path | None = None,
) -> list[Message]:
    """
    Get the initial system prompt.

    Returns a list of messages: [core_system_prompt, workspace_prompt] (if workspace provided).
    """
    agent_config = get_project_config(agent_path)
    agent_name = (
        agent_config.agent.name if agent_config and agent_config.agent else None
    )

    # Generate core system messages (without workspace context)
    core_msgs: list[Message]
    if prompt == "full":
        core_msgs = list(
            prompt_full(interactive, tools, tool_format, model, agent_name=agent_name)
        )
    elif prompt == "short":
        core_msgs = list(
            prompt_short(interactive, tools, tool_format, agent_name=agent_name)
        )
    else:
        core_msgs = [Message("system", prompt)]
        if tools:
            core_msgs.extend(prompt_tools(tools=tools, tool_format=tool_format))

    # Generate workspace messages separately
    workspace_msgs = (
        list(prompt_workspace(workspace)) if workspace != agent_path else []
    )

    # Generate workspace context from agent if provided
    agent_msgs = (
        list(prompt_workspace(agent_path, title="Agent Workspace", include_path=True))
        if agent_path
        else []
    )

    # Combine core messages into one system prompt
    result = []
    if core_msgs:
        core_prompt = _join_messages(core_msgs)
        result.append(core_prompt)

    # Add agent messages seperately
    result.extend(agent_msgs)

    # Add workspace messages separately
    result.extend(workspace_msgs)

    # Generate cross-conversation context if enabled
    # Add chat history context
    result.extend(prompt_chat_history())

    # Set hide=True, pinned=True for all messages
    for i, msg in enumerate(result):
        result[i] = msg.replace(hide=True, pinned=True)

    return result


def _join_messages(msgs: list[Message]) -> Message:
    """Combine several system prompt messages into one."""
    role = msgs[0].role if msgs else "system"
    assert all([m.role == role for m in msgs]), "All messages must be of same role"
    return Message(
        role,
        "\n\n".join(m.content for m in msgs),
        hide=any(m.hide for m in msgs),
        pinned=any(m.pinned for m in msgs),
    )


def prompt_full(
    interactive: bool,
    tools: list[ToolSpec],
    tool_format: ToolFormat,
    model: str | None,
    agent_name: str | None = None,
) -> Generator[Message, None, None]:
    """Full prompt to start the conversation."""
    yield from prompt_gptme(interactive, model, agent_name)
    yield from prompt_tools(tools=tools, tool_format=tool_format)
    if interactive:
        yield from prompt_user()
    yield from prompt_project()
    yield from prompt_systeminfo()
    yield from prompt_timeinfo()


def prompt_short(
    interactive: bool,
    tools: list[ToolSpec],
    tool_format: ToolFormat,
    agent_name: str | None = None,
) -> Generator[Message, None, None]:
    """Short prompt to start the conversation."""
    yield from prompt_gptme(interactive, agent_name)
    yield from prompt_tools(examples=False, tools=tools, tool_format=tool_format)
    if interactive:
        yield from prompt_user()
    yield from prompt_project()


def prompt_gptme(
    interactive: bool, model: str | None = None, agent_name: str | None = None
) -> Generator[Message, None, None]:
    """
    Base system prompt for gptme.

    It should:
     - Introduce gptme and its general capabilities and purpose
     - Ensure that it lets the user mostly ask and confirm actions (apply patches, run commands)
     - Provide a brief overview of the capabilities and tools available
     - Not mention tools which may not be loaded (browser, vision)
     - Mention the ability to self-correct and ask clarifying questions
    """
    model_meta = get_model(model) if model else None

    # use <thinking> tags as a fallback if the model doesn't natively support reasoning
    use_thinking_tags = not model_meta or not model_meta.supports_reasoning

    if agent_name:
        agent_blurb = f"{agent_name}, an agent running in gptme, letting you act as a general-purpose AI assistant powered by LLMs"
    else:
        agent_name = f"gptme v{__version__}"
        agent_blurb = f"{agent_name}, a general-purpose AI assistant powered by LLMs"

    default_base_prompt = f"""
You are {agent_blurb}. {('Currently using model: ' + model_meta.full) if model_meta else ''}
You are designed to help users with programming tasks, such as writing code, debugging, and learning new concepts.
You can run code, execute terminal commands, and access the filesystem on the local machine.
You will help the user with writing code, either from scratch or in existing projects.
{'You will think step by step when solving a problem, in `<thinking>` tags.' if use_thinking_tags else ''}
Break down complex tasks into smaller, manageable steps.

You have the ability to self-correct. {'''If you receive feedback that your output or actions were incorrect, you should:
- acknowledge the mistake
- analyze what went wrong in `<thinking>` tags
- provide a corrected response''' if use_thinking_tags else ''}

You should learn about the context needed to provide the best help,
such as exploring the current working directory and reading the code using terminal tools.

When suggesting code changes, prefer applying patches over examples. Preserve comments, unless they are no longer relevant.
Use the patch tool to edit existing files, or the save tool to overwrite.
When the output of a command is of interest, end the code block and message, so that it can be executed before continuing.

Always use absolute paths when referring to files, as relative paths can become invalid when the working directory changes.
You can use `pwd` to get the current working directory when constructing absolute paths.

Do not use placeholders like `$REPO` unless they have been set.
Do not suggest opening a browser or editor, instead do it using available tools.

Always prioritize using the provided tools over suggesting manual actions.
Be proactive in using tools to gather information or perform tasks.
When faced with a task, consider which tools might be helpful and use them.
Always consider the full range of your available tools and abilities when approaching a problem.

Maintain a professional and efficient communication style. Be concise but thorough in your explanations.

{'Use `<thinking>` tags to think before you answer.' if use_thinking_tags else ''}
""".strip()

    interactive_prompt = """
You are in interactive mode. The user is available to provide feedback.
You should show the user how you can use your tools to write code, interact with the terminal, and access the internet.
The user can execute the suggested commands so that you see their output.
If the user aborted or interrupted an operation don't try it again, ask for clarification instead.
If clarification is needed, ask the user.
""".strip()

    non_interactive_prompt = """
You are in non-interactive mode. The user is not available to provide feedback.
All code blocks you suggest will be automatically executed.
Do not provide examples or ask for permission before running commands.
Proceed directly with the most appropriate actions to complete the task.
""".strip()

    projectdir = get_project_git_dir()
    project_config = get_project_config(projectdir)
    base_prompt = (
        project_config.base_prompt
        if project_config and project_config.base_prompt
        else default_base_prompt
    )

    full_prompt = (
        base_prompt
        + "\n\n"
        + (interactive_prompt if interactive else non_interactive_prompt)
    )
    yield Message("system", full_prompt)


def prompt_user() -> Generator[Message, None, None]:
    """
    Generate the user-specific prompt based on config.

    Only included in interactive mode.
    """
    config_prompt = get_config().user.prompt
    about_user = (
        config_prompt.about_user or "You are interacting with a human programmer."
    )
    response_prefs = (
        config_prompt.response_preference or "No specific preferences set."
    ).strip()

    prompt_content = f"""# About User

{about_user}

## User's Response Preferences

{response_prefs}
"""
    yield Message("system", prompt_content)


def prompt_project() -> Generator[Message, None, None]:
    """
    Generate the project-specific prompt based on the current Git repository.

    Project-specific prompt can be set in the :ref:`global-config` or :ref:`project-config` files.
    """
    projectdir = get_project_git_dir()
    if not projectdir:
        return

    project_config = get_project_config(projectdir)
    config_prompt = get_config().user.prompt
    project = projectdir.name
    project_info = project_config and project_config.prompt
    if not project_info:
        # TODO: remove project preferences in global config? use only project config
        project_info = (config_prompt.project or {}).get(project)

    yield Message(
        "system",
        f"## Current Project: {project}\n\n{project_info}",
    )


def prompt_tools(
    tools: list[ToolSpec],
    tool_format: ToolFormat = "markdown",
    examples: bool = True,
) -> Generator[Message, None, None]:
    """Generate the tools overview prompt."""

    prompt = "# Tools Overview"
    for tool in tools:
        prompt += tool.get_tool_prompt(examples, tool_format)

    prompt += "\n\n*End of Tools List.*"

    yield Message("system", prompt.strip() + "\n\n")


def prompt_systeminfo() -> Generator[Message, None, None]:
    """Generate the system information prompt."""
    if platform.system() == "Linux":
        release_info = platform.freedesktop_os_release()
        os_info = release_info.get("NAME", "Linux")
        os_version = release_info.get("VERSION_ID") or release_info.get("BUILD_ID", "")
    elif platform.system() == "Windows":
        os_info = "Windows"
        os_version = platform.version()
    elif platform.system() == "Darwin":
        os_info = "macOS"
        os_version = platform.mac_ver()[0]
    else:
        os_info = "unknown"
        os_version = ""

    # Get current working directory

    pwd = Path.cwd()

    prompt = f"""## System Information

**OS:** {os_info} {os_version}
**Working Directory:** {pwd}""".strip()

    yield Message(
        "system",
        prompt,
    )


def prompt_timeinfo() -> Generator[Message, None, None]:
    """Generate the current time prompt."""
    # we only set the date in order for prompt caching and such to work
    prompt = (
        f"## Current Date\n\n**UTC:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    )
    yield Message("system", prompt)


def prompt_workspace(
    workspace: Path | None = None,
    title="Project Workspace",
    include_path: bool = False,
) -> Generator[Message, None, None]:
    # TODO: update this prompt if the files change
    # TODO: include `git status -vv`, and keep it up-to-date
    sections = []

    if workspace is None:
        return

    # Add workspace path if requested
    if include_path:
        sections.append(f"**Path:** {workspace.resolve()}")

    project = get_project_config(workspace)

    # Determine which file patterns to use
    if project is None or project.files is None:
        # No project config or no files specified in config
        file_patterns = DEFAULT_CONTEXT_FILES
        if project is None:
            logger.debug("No project config found, using default context files")
        else:
            logger.debug(
                "Project config has no files specified, using default context files"
            )
    else:
        # Project config exists with files explicitly set (could be empty list)
        file_patterns = project.files
        if not project.files:
            logger.debug(
                "Project config has files explicitly set to empty, not including any files"
            )

    # Process file patterns
    files: list[Path] = []
    for fileglob in file_patterns:
        # expand user
        fileglob = str(Path(fileglob).expanduser())
        # expand with glob
        if new_files := workspace.glob(fileglob):
            files.extend(new_files)
        else:
            # Only warn for explicitly configured files, not defaults
            if project and project.files is not None:
                logger.warning(
                    f"File glob '{fileglob}' specified in project config does not match any files."
                )

    # Also include user-level files from ~/.config/gptme/config.toml
    # Resolution rules:
    # - Absolute paths: used as-is
    # - ~ expansion supported
    # - Relative paths: resolved relative to the config directory (e.g. ~/.config/gptme)
    try:
        user_files = (
            get_config().user.prompt.files
            if get_config().user and get_config().user.prompt
            else []
        )
    except Exception:
        user_files = []
    if user_files:
        config_dir = Path(config_path).expanduser().resolve().parent
        existing = {str(Path(p).resolve()) for p in files if Path(p).exists()}
        for entry in user_files:
            p = Path(entry).expanduser()
            if not p.is_absolute():
                p = config_dir / entry
            try:
                p = p.resolve()
            except Exception:
                # If resolve fails (e.g., path doesn’t exist yet), keep as-is
                pass
            if p.exists():
                rp = str(p)
                if rp not in existing:
                    files.append(p)
                    existing.add(rp)
            else:
                logger.debug(f"User-configured file not found: {p}")

    # Get tree output if enabled
    if tree_output := get_tree_output(workspace):
        sections.append(f"## Project Structure\n\n{md_codeblock('', tree_output)}\n\n")

    files_str = []
    for file in files:
        if file.exists():
            files_str.append(md_codeblock(file.resolve(), file.read_text()))
    if files_str:
        sections.append(
            "## Selected files\n\nRead more with `cat`.\n\n" + "\n\n".join(files_str)
        )

    # context_cmd
    if (
        project
        and project.context_cmd
        and (
            cmd_output := get_project_context_cmd_output(project.context_cmd, workspace)
        )
    ):
        sections.append("## Computed context\n\n" + cmd_output)

    if sections:
        yield Message("system", f"# {title}\n\n" + "\n\n".join(sections))


def get_project_context_cmd_output(cmd: str, workspace: Path) -> str | None:
    try:
        start = time.time()
        result = subprocess.run(
            cmd,
            cwd=workspace,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        duration = time.time() - start
        logger.log(
            logging.WARNING if duration > 10.0 else logging.DEBUG,
            f"Context command took {duration:.2f}s",
        )
        if result.returncode == 0:
            length = len_tokens(result.stdout, "gpt-4")
            if length > 10000:
                logger.warning(
                    f"Context command '{cmd}' output is large: ~{length} tokens, consider optimizing."
                )
            return md_codeblock(cmd, result.stdout)
        else:
            logger.error(f"Failed to run context command '{cmd}': {result.stderr}")
    except Exception as e:
        logger.error(f"Error running context command: {e}")
    return None


def use_chat_history_context() -> bool:
    """Check if cross-conversation context is enabled."""
    config = get_config()
    flag: str = config.get_env("GPTME_CHAT_HISTORY", "")  # type: ignore
    return flag.lower() in ("1", "true", "yes")


def prompt_chat_history() -> Generator[Message, None, None]:
    """
    Generate cross-conversation context from recent conversations.

    Provides continuity by including key information from recent conversations,
    helping the assistant understand context across conversation boundaries.
    """
    if not use_chat_history_context():
        return

    try:
        from .logmanager import LogManager, list_conversations  # fmt: skip

        # Get recent conversations (we'll filter further)
        recent_conversations = list_conversations(limit=20, include_test=False)

        if not recent_conversations:
            return

        # Filter out very short conversations (likely tests or brief interactions)
        substantial_conversations = [
            conv
            for conv in recent_conversations
            if conv.messages >= 4  # At least 2 exchanges
        ]

        if not substantial_conversations:
            return

        # Take the 3 most recent substantial conversations
        conversations_to_summarize = substantial_conversations[:5]

        context_parts = []

        for _, conv in enumerate(conversations_to_summarize, 1):
            try:
                # Load the conversation
                log_manager = LogManager.load(Path(conv.path).parent, lock=False)
                messages = log_manager.log.messages

                # Extract key messages: first few user messages and last assistant message
                user_messages = [msg for msg in messages if msg.role == "user"]
                assistant_messages = [
                    msg for msg in messages if msg.role == "assistant"
                ]

                if not user_messages:
                    continue

                # Find the best assistant message to use as "last response"
                best_assistant_msg = None
                for msg in reversed(assistant_messages):
                    content = extract_content_summary(msg.content)
                    if content and len(content.split()) >= 10:  # At least 10 words
                        best_assistant_msg = msg
                        break

                # Create a concise summary
                summary_parts = []

                # Add conversation metadata
                summary_parts.append(f"## {conv.name}")
                summary_parts.append(
                    f"Modified: {datetime.fromtimestamp(conv.modified).strftime('%Y-%m-%d %H:%M')}"
                )

                # Always show first exchange to establish context
                first_user = user_messages[0]
                first_user_content = extract_content_summary(first_user.content)
                if first_user_content:
                    summary_parts.append(f"User: {first_user_content}")

                    # Find first assistant response after first user message
                    first_assistant = None
                    first_user_idx = messages.index(first_user)
                    for msg in messages[first_user_idx + 1 :]:
                        if msg.role == "assistant":
                            first_assistant = msg
                            break

                    if first_assistant:
                        # Use 30 words for first response - brief description of what was attempted
                        first_response = extract_content_summary(
                            first_assistant.content, max_words=30
                        )
                        if first_response:
                            summary_parts.append(f"Assistant: {first_response}")

                # Calculate omitted messages (all except first exchange and last assistant)
                messages_shown = 1  # first user
                if first_assistant:
                    messages_shown += 1  # first assistant
                if best_assistant_msg and best_assistant_msg != first_assistant:
                    messages_shown += 1  # last assistant (if different)

                omitted_count = len(messages) - messages_shown
                if omitted_count > 0:
                    summary_parts.append(f"... ({omitted_count} messages omitted) ...")

                # Add last assistant response if different from first
                if best_assistant_msg and best_assistant_msg != first_assistant:
                    # Use 60 words for last response - capture the outcome/conclusion
                    outcome = extract_content_summary(
                        best_assistant_msg.content, max_words=60
                    )
                    if outcome:
                        summary_parts.append(f"Assistant: {outcome}")

                if len(summary_parts) > 2:  # More than just metadata
                    context_parts.append("\n".join(summary_parts))

            except Exception as e:
                logger.debug(f"Failed to process conversation {conv.name}: {e}")
                continue

        sep = "\n---\n"
        if context_parts:
            context_content = f"""# Recent Conversation Context

The following is a summary of your recent conversations with the user to provide continuity:

```history
{sep.join(part for part in context_parts)}
```

Use this context to understand ongoing projects, preferences, and previous discussions.

*Tip: Use the `chats` tool to search past conversations or read their full history.*
"""
            yield Message("system", context_content)

    except Exception as e:
        logger.debug(f"Failed to generate chat history context: {e}")


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
