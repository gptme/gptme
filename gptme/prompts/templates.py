import logging
import platform
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from ..config import get_config, get_project_config
from ..dirs import get_project_git_dir
from ..llm.models import get_model
from ..message import Message
from ..tools import ToolFormat, ToolSpec
from . import _xml_section
from .skills import prompt_skills_summary

logger = logging.getLogger(__name__)


def prompt_full(
    interactive: bool,
    tools: list[ToolSpec],
    tool_format: ToolFormat,
    model: str | None,
    agent_name: str | None = None,
    workspace: Path | None = None,
) -> Generator[Message, None, None]:
    """Full prompt to start the conversation."""
    yield from prompt_gptme(interactive, model, agent_name, tool_format=tool_format)
    yield from prompt_tools(tools=tools, tool_format=tool_format, model=model)
    if interactive:
        yield from prompt_user(tool_format=tool_format)
    yield from prompt_project(tool_format=tool_format)
    yield from prompt_systeminfo(workspace, tool_format=tool_format)
    yield from prompt_timeinfo(tool_format=tool_format)
    yield from prompt_skills_summary(tool_format=tool_format)


def prompt_short(
    interactive: bool,
    tools: list[ToolSpec],
    tool_format: ToolFormat,
    agent_name: str | None = None,
) -> Generator[Message, None, None]:
    """Short prompt to start the conversation."""
    yield from prompt_gptme(interactive, agent_name=agent_name, tool_format=tool_format)
    yield from prompt_tools(examples=False, tools=tools, tool_format=tool_format)
    if interactive:
        yield from prompt_user(tool_format=tool_format)
    yield from prompt_project(tool_format=tool_format)


def prompt_gptme(
    interactive: bool,
    model: str | None = None,
    agent_name: str | None = None,
    tool_format: ToolFormat = "markdown",
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
        from ..__version__ import __version__

        agent_name = f"gptme v{__version__}"
        agent_blurb = f"{agent_name}, a general-purpose AI assistant powered by LLMs"

    default_base_prompt = f"""
You are {agent_blurb}. {
        ("Currently using model: " + model_meta.full) if model_meta else ""
    }
You are designed to help users with programming tasks, such as writing code, debugging, and learning new concepts.
You can run code, execute terminal commands, and access the filesystem on the local machine.
You will help the user with writing code, either from scratch or in existing projects.
{
        "You will think step by step when solving a problem, in `<thinking>` tags."
        if use_thinking_tags
        else ""
    }
Break down complex tasks into smaller, manageable steps.

You have the ability to self-correct. {
        '''If you receive feedback that your output or actions were incorrect, you should:
- acknowledge the mistake
- analyze what went wrong in `<thinking>` tags
- provide a corrected response'''
        if use_thinking_tags
        else ""
    }

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

{"Use `<thinking>` tags to think before you answer." if use_thinking_tags else ""}
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
    if tool_format == "xml":
        full_prompt = _xml_section("role", xml_escape(full_prompt))
    yield Message("system", full_prompt)


def prompt_user(
    tool_format: ToolFormat = "markdown",
) -> Generator[Message, None, None]:
    """
    Generate the user-specific prompt based on config.

    Only included in interactive mode.
    Reads from ``[user]`` section first, falling back to ``[prompt]`` for backward compat.
    """
    config = get_config()
    user_identity = config.user.user
    config_prompt = config.user.prompt

    # Prefer [user] section, fall back to [prompt] for backward compat
    about_user = (
        user_identity.about
        or config_prompt.about_user
        or "You are interacting with a human programmer."
    )
    response_prefs = (
        user_identity.response_preference
        or config_prompt.response_preference
        or "No specific preferences set."
    ).strip()

    user_name = user_identity.name or "User"

    if tool_format == "xml":
        prompt_content = _xml_section(
            "user",
            f"<name>{xml_escape(user_name)}</name>\n"
            f"<about>{xml_escape(about_user)}</about>\n"
            f"<response-preferences>{xml_escape(response_prefs)}</response-preferences>",
        )
    else:
        prompt_content = f"""# About {user_name}

{about_user}

## {user_name}'s Response Preferences

{response_prefs}
"""
    yield Message("system", prompt_content)


def prompt_project(
    tool_format: ToolFormat = "markdown",
) -> Generator[Message, None, None]:
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

    if tool_format == "xml":
        content = f"<name>{xml_escape(project)}</name>\n{'<info>' + xml_escape(project_info) + '</info>' if project_info else ''}"
        yield Message("system", _xml_section("project", content))
    else:
        info_section = f"\n\n{project_info}" if project_info else ""
        yield Message(
            "system",
            f"## Current Project: {project}{info_section}",
        )


def prompt_tools(
    tools: list[ToolSpec],
    tool_format: ToolFormat = "markdown",
    examples: bool = True,
    model: str | None = None,
) -> Generator[Message, None, None]:
    """Generate the tools overview prompt.

    For reasoning models using native tool-calling (tool_format="tool"), examples are skipped
    per OpenAI best practices for function calling:
    https://platform.openai.com/docs/guides/function-calling#best-practices-for-defining-functions

    For text-based formats (markdown/xml), examples are kept even for reasoning models,
    since they serve as documentation in the system prompt rather than few-shot examples.
    """
    # Only skip examples for native tool-calling format with reasoning models.
    # For markdown/xml, examples are part of the system prompt text and still useful
    # as documentation. The OpenAI guideline specifically targets native function schemas.
    if examples and model and tool_format == "tool":
        model_meta = get_model(model)
        if model_meta.supports_reasoning:
            logger.debug(
                "Skipping tool examples for reasoning model %s (native tool-calling format)",
                model,
            )
            examples = False

    if tool_format == "xml":
        prompt = "<tools>"
        for tool in tools:
            prompt += tool.get_tool_prompt(examples, tool_format)
        prompt += "\n</tools>"
    else:
        prompt = "# Tools Overview"
        for tool in tools:
            prompt += tool.get_tool_prompt(examples, tool_format)
        prompt += "\n\n*End of Tools List.*"

    yield Message("system", prompt.strip() + "\n\n")


def prompt_systeminfo(
    workspace: Path | None = None,
    tool_format: ToolFormat = "markdown",
) -> Generator[Message, None, None]:
    """Generate the system information prompt."""
    if platform.system() == "Linux":
        try:
            release_info = platform.freedesktop_os_release()
        except OSError:
            release_info = {}
        os_info = release_info.get("NAME", "Linux")
        os_version = (
            release_info.get("VERSION_ID")
            or release_info.get("BUILD_ID")
            or platform.release()
        )
    elif platform.system() == "Windows":
        os_info = "Windows"
        os_version = platform.version()
    elif platform.system() == "Darwin":
        os_info = "macOS"
        os_version = platform.mac_ver()[0]
    else:
        os_info = "unknown"
        os_version = ""

    # Get current working directory (use provided workspace if available)
    pwd = workspace or Path.cwd()

    if tool_format == "xml":
        prompt = _xml_section(
            "system-info",
            f"<os>{xml_escape(f'{os_info} {os_version}')}</os>\n"
            f"<working-directory>{xml_escape(str(pwd))}</working-directory>",
        )
    else:
        prompt = f"""## System Information

**OS:** {os_info} {os_version}
**Working Directory:** {pwd}""".strip()

    yield Message(
        "system",
        prompt,
    )


def prompt_timeinfo(
    tool_format: ToolFormat = "markdown",
) -> Generator[Message, None, None]:
    """Generate the current time prompt."""
    # we only set the date in order for prompt caching and such to work
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if tool_format == "xml":
        prompt = _xml_section("current-date", date_str)
    else:
        prompt = f"## Current Date\n\n**UTC:** {date_str}"
    yield Message("system", prompt)
