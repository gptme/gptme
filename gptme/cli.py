import logging
import os
import signal
import sys
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Literal

import click
from pick import pick

from . import __version__
from .chat import chat
from .commands import _gen_help
from .config import (
    ChatConfig,
    get_config,
    set_config,
    set_config_from_workspace,
)
from .constants import MULTIPROMPT_SEPARATOR
from .dirs import get_logs_dir
from .init import init_logging
from .llm.models import get_recommended_model
from .logmanager import ConversationMeta, get_user_conversations
from .message import Message
from .prompts import get_prompt
from .tools import ToolFormat, get_available_tools, init_tools
from .util import epoch_to_age
from .util.generate_name import generate_name
from .util.interrupt import handle_keyboard_interrupt, set_interruptible
from .util.prompt import add_history

logger = logging.getLogger(__name__)


script_path = Path(os.path.realpath(__file__))
commands_help = "\n".join(_gen_help(incl_langtags=False))
available_tool_names = ", ".join(
    sorted([tool.name for tool in get_available_tools() if tool.is_available])
)


docstring = f"""
gptme is a chat-CLI for LLMs, empowering them with tools to run shell commands, execute code, read and manipulate files, and more.

If PROMPTS are provided, a new conversation will be started with it.
PROMPTS can be chained with the '{MULTIPROMPT_SEPARATOR}' separator.

The interface provides user commands that can be used to interact with the system.

\b
{commands_help}"""


@click.command(help=docstring)
@click.argument(
    "prompts",
    default=None,
    required=False,
    nargs=-1,
)
@click.option(
    "--name",
    default="random",
    help="Name of conversation. Defaults to generating a random name.",
)
@click.option(
    "-m",
    "--model",
    default=None,
    help=f"Model to use, e.g. openai/{get_recommended_model('openai')}, anthropic/{get_recommended_model('anthropic')}. If only provider given then a default is used.",
)
@click.option(
    "-w",
    "--workspace",
    default=None,
    help="Path to workspace directory. Pass '@log' to create a workspace in the log directory.",
)
@click.option(
    "-r",
    "--resume",
    is_flag=True,
    help="Load last conversation",
)
@click.option(
    "-y",
    "--no-confirm",
    is_flag=True,
    help="Skips all confirmation prompts.",
)
@click.option(
    "-n",
    "--non-interactive",
    "interactive",
    default=True,
    flag_value=False,
    help="Non-interactive mode. Implies --no-confirm.",
)
@click.option(
    "--system",
    "prompt_system",
    default="full",
    help="System prompt. Can be 'full', 'short', or something custom.",
)
@click.option(
    "-t",
    "--tools",
    "tool_allowlist",
    default=None,
    multiple=True,
    help=f"Comma-separated list of tools to allow. Available: {available_tool_names}.",
)
@click.option(
    "--tool-format",
    "tool_format",
    default=None,
    help="Tool parsing method. Can be 'markdown', 'xml', 'tool'. (experimental)",
)
@click.option(
    "--no-stream",
    "stream",
    default=True,
    flag_value=False,
    help="Don't stream responses",
)
@click.option(
    "--show-hidden",
    is_flag=True,
    help="Show hidden system messages.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show verbose output.",
)
@click.option(
    "--version",
    is_flag=True,
    help="Show version and configuration information",
)
def main(
    prompts: list[str],
    prompt_system: str,
    name: str,
    model: str | None,
    tool_allowlist: list[str] | None,
    tool_format: ToolFormat | None,
    stream: bool,
    verbose: bool,
    no_confirm: bool,
    interactive: bool,
    show_hidden: bool,
    version: bool,
    resume: bool,
    workspace: str | None,
):
    """Main entrypoint for the CLI."""
    if version:
        # print version

        print(f"gptme v{__version__}")

        # print dirs
        print(f"Logs dir: {get_logs_dir()}")

        exit(0)

    if "PYTEST_CURRENT_TEST" in os.environ:
        interactive = False

    # init logging
    init_logging(verbose)

    if not interactive:
        no_confirm = True

    if no_confirm:
        logger.warning("Skipping all confirmation prompts.")

    # if stdin is not a tty, we might be getting piped input, which we should include in the prompt
    was_piped = False
    piped_input = None
    if not sys.stdin.isatty():
        # fetch prompt from stdin
        piped_input = _read_stdin()
        if piped_input:
            was_piped = True

            # Attempt to switch to interactive mode
            # https://github.com/prompt-toolkit/python-prompt-toolkit/issues/502#issuecomment-466591259
            sys.stdin = sys.stdout

            # Old code, doesn't work with prompt-toolkit
            # sys.stdin.close()
            # try:
            #     sys.stdin = open("/dev/tty")
            # except OSError:
            #     # if we can't open /dev/tty, we're probably in a CI environment, so we should just continue
            #     logger.warning(
            #         "Failed to switch to interactive mode, continuing in non-interactive mode"
            #     )

    # add prompts to prompt-toolkit history
    for prompt in prompts:
        if prompt and len(prompt) > 1000:
            # skip adding long prompts to history (slows down startup, unlikely to be useful)
            continue
        add_history(prompt)

    # join prompts, grouped by `-` if present, since that's the separator for "chained"/multiple-round prompts
    sep = "\n\n" + MULTIPROMPT_SEPARATOR
    prompts = [p.strip() for p in "\n\n".join(prompts).split(sep) if p]
    # TODO: referenced file paths in multiprompts should be read when run, not when parsed
    prompt_msgs = [Message("user", p) for p in prompts]

    def inject_stdin(prompt_msgs, piped_input: str | None) -> list[Message]:
        # if piped input, append it to first prompt, or create a new prompt if none exists
        if not piped_input:
            return prompt_msgs
        stdin_msg = Message("user", f"```stdin\n{piped_input}\n```")
        if not prompt_msgs:
            prompt_msgs.append(stdin_msg)
        else:
            prompt_msgs[0] = prompt_msgs[0].replace(
                content=f"{prompt_msgs[0].content}\n\n{stdin_msg.content}"
            )
        return prompt_msgs

    if resume:
        logdir = get_logdir_resume()
        prompt_msgs = inject_stdin(prompt_msgs, piped_input)
    # don't run pick in tests/non-interactive mode, or if the user specifies a name
    elif (
        interactive
        and name == "random"
        and not prompt_msgs
        and not was_piped
        and sys.stdin.isatty()
    ):
        logdir = pick_log()
    else:
        logdir = get_logdir(name)
        prompt_msgs = inject_stdin(prompt_msgs, piped_input)

    if workspace == "@log":
        workspace_path: Path | None = logdir / "workspace"
        assert workspace_path  # mypy not smart enough to see its not None
        workspace_path.mkdir(parents=True, exist_ok=True)
    else:
        workspace_path = Path(workspace) if workspace else Path.cwd()

    # Parse tool allowlist cli argument.
    if tool_allowlist:
        # split comma-separated values
        tool_allowlist = [tool for tools in tool_allowlist for tool in tools.split(",")]

    # Load main config
    set_config_from_workspace(workspace_path)
    config = get_config()

    # Load or create chat config, applying CLI overrides
    logdir.mkdir(parents=True, exist_ok=True)
    chat_config = ChatConfig.load_or_create(
        logdir=logdir,
        cli_config=ChatConfig(
            model=model,
            tools=tool_allowlist,
            tool_format=tool_format,
            stream=stream,
            interactive=interactive,
            workspace=workspace_path,
        ),
    ).save()

    # Set chat config in main config
    config.chat = chat_config
    set_config(config)

    model = model or config.get_env("MODEL")
    selected_tool_format: ToolFormat = (
        tool_format or config.get_env("TOOL_FORMAT") or "markdown"  # type: ignore
    )

    # early init tools to generate system prompt
    # We pass the tool_allowlist CLI argument. If it's not provided, init_tools
    # will load it from the environment variable TOOL_ALLOWLIST or the chat config.
    tools = init_tools(tool_allowlist)

    # get initial system prompt
    initial_msgs = [
        get_prompt(
            tools=tools,
            prompt=prompt_system,
            interactive=interactive,
            tool_format=selected_tool_format,
            model=model,
        )
    ]

    # register a handler for Ctrl-C
    set_interruptible()  # prepare, user should be able to Ctrl+C until user prompt ready
    signal.signal(signal.SIGINT, handle_keyboard_interrupt)

    try:
        chat(
            prompt_msgs,
            initial_msgs,
            logdir,
            chat_config.model,
            chat_config.stream,
            no_confirm,
            chat_config.interactive,
            show_hidden,
            chat_config.workspace,
            chat_config.tools,
            chat_config.tool_format,
        )
    except RuntimeError as e:
        if verbose:
            logger.exception(e)
        else:
            logger.error(e)
        sys.exit(1)


def get_name(name: str) -> str:
    """
    Returns a name for the new conversation.

    If name is "random", generates a random name.
    If name is starts with a date, uses it as is.
    Otherwise, prepends the current date to the name.
    """
    datestr = datetime.now().strftime("%Y-%m-%d")
    logsdir = get_logs_dir()

    # returns a name for the new conversation
    if name == "random":
        # check if name exists, if so, generate another one
        for _ in range(3):
            name = generate_name()
            name = f"{datestr}-{name}"
            logpath = logsdir / name
            if not logpath.exists():
                break
        else:
            raise ValueError("Failed to generate unique name")
    else:
        # if name starts with date, use as is
        try:
            datetime.strptime(name[:10], "%Y-%m-%d")
        except ValueError:
            name = f"{datestr}-{name}"
    return name


def pick_log(limit=20) -> Path:  # pragma: no cover
    # let user select between starting a new conversation and loading a previous one
    # using the library
    title = "New conversation or load previous? "
    NEW_CONV = "New conversation"
    LOAD_MORE = "Load more"
    gen_convs = get_user_conversations()
    convs: list[ConversationMeta] = []

    # load conversations
    convs.extend(islice(gen_convs, limit))

    # filter out test conversations
    # TODO: save test convos to different folder instead
    # def is_test(name: str) -> bool:
    #     return "-test-" in name or name.startswith("test-")
    # prev_conv_files = [f for f in prev_conv_files if not is_test(f.parent.name)]

    terminal_width = os.get_terminal_size().columns

    prev_convs: list[str] = []
    for conv in convs:
        name = conv.name
        metadata = f"{epoch_to_age(conv.modified)}  {conv.messages:4d} msgs"
        spacing = terminal_width - len(name) - len(metadata) - 6
        prev_convs.append(" ".join([name, spacing * " ", metadata]))

    options = (
        [
            NEW_CONV,
        ]
        + prev_convs
        + [LOAD_MORE]
    )

    index: int
    _, index = pick(options, title)  # type: ignore
    if index == 0:
        return get_logdir("random")
    elif index == len(options) - 1:
        return pick_log(limit + 100)
    else:
        return get_logdir(convs[index - 1].name)


def get_logdir(logdir: Path | str | Literal["random"]) -> Path:
    if logdir == "random":
        logdir = get_logs_dir() / get_name("random")
    elif isinstance(logdir, str):
        logdir = get_logs_dir() / logdir

    logdir.mkdir(parents=True, exist_ok=True)
    return logdir


def get_logdir_resume() -> Path:
    if conv := next(get_user_conversations(), None):
        return Path(conv.path).parent
    else:
        raise ValueError("No previous conversations to resume")


def _read_stdin() -> str:
    chunk_size = 1024  # 1 KB
    all_data = ""

    while True:
        chunk = sys.stdin.read(chunk_size)
        if not chunk:
            break
        all_data += chunk

    return all_data
