import atexit
import logging
import os
import signal
import sys
from itertools import islice
from pathlib import Path
from typing import Literal

import click
from pick import pick

from . import __version__
from .chat import chat
from .commands import _gen_help
from .config import setup_config_from_cli
from .constants import MULTIPROMPT_SEPARATOR
from .dirs import get_logs_dir
from .init import init_logging
from .llm.models import get_recommended_model
from .logmanager import ConversationMeta, get_user_conversations
from .message import Message
from .prompts import get_prompt
from .telemetry import init_telemetry, shutdown_telemetry
from .tools import ToolFormat, get_available_tools, init_tools
from .util import epoch_to_age
from .util.auto_naming import generate_conversation_id
from .util.interrupt import handle_keyboard_interrupt, set_interruptible
from .util.prompt import add_history

logger = logging.getLogger(__name__)


script_path = Path(os.path.realpath(__file__))
commands_help = "\n".join(_gen_help(incl_langtags=False))
available_tool_names = ", ".join(
    sorted(
        [
            tool.name
            for tool in get_available_tools(include_mcp=False)
            if tool.is_available
        ]
    )
)


docstring = f"""
gptme is a chat-CLI for LLMs, empowering them with tools to run shell commands, execute code, read and manipulate files, and more.

If PROMPTS are provided, a new conversation will be started with it.
PROMPTS can be chained with the '{MULTIPROMPT_SEPARATOR}' separator.

The interface provides user commands that can be used to interact with the system.

\b
{commands_help}"""


@click.command(help=docstring)
@click.pass_context
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
    "workspace",
    default=None,
    help="Path to workspace directory. Pass '@log' to create a workspace in the log directory.",
)
@click.option(
    "--agent-path",
    "agent_path",
    default=None,
    help="Path to agent workspace directory.",
)
@click.option(
    "-r",
    "--resume",
    is_flag=True,
    help="Load most recent conversation.",
)
@click.option(
    "-y",
    "--no-confirm",
    is_flag=True,
    help="Skip all confirmation prompts.",
)
@click.option(
    "-n",
    "--non-interactive",
    "non_interactive",
    is_flag=True,
    help="Non-interactive mode. Implies --no-confirm.",
)
@click.option(
    "--system",
    "prompt_system",
    default="full",
    help="System prompt. Options: 'full', 'short', or something custom.",
)
@click.option(
    "-t",
    "--tools",
    "tool_allowlist",
    default=None,
    multiple=True,
    help=f"Tools to allow. Can be specified multiple times or comma-separated. Use '+tool' to add to defaults (e.g., '-t +subagent'). Available: {available_tool_names}.",
)
@click.option(
    "--tool-format",
    "tool_format",
    default=None,
    help="Tool format to use. Options: markdown, xml, tool",
)
@click.option(
    "--stream/--no-stream",
    "stream",
    default=True,
    help="Stream responses",
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
@click.option(
    "--profile",
    is_flag=True,
    help="Enable profiling and save results to gptme-profile-{timestamp}.prof",
)
def main(
    ctx: click.Context,
    prompts: list[str],
    prompt_system: str,
    name: str,
    model: str | None,
    tool_allowlist: tuple[str, ...],
    tool_format: ToolFormat | None,
    stream: bool,
    verbose: bool,
    no_confirm: bool,
    non_interactive: bool,
    show_hidden: bool,
    version: bool,
    resume: bool,
    workspace: str | None,
    agent_path: str | None,
    profile: bool,
):
    """Main entrypoint for the CLI."""
    # Convert tool_allowlist from tuple to string or None
    # Use get_parameter_source to distinguish between default (None) and explicit empty list
    from click.core import ParameterSource

    tool_allowlist_str: str | None
    if ctx.get_parameter_source("tool_allowlist") == ParameterSource.DEFAULT:
        # Not provided by user, use None to indicate "use defaults"
        tool_allowlist_str = None
    elif tool_allowlist:
        # User provided tools - flatten any comma-separated values and join
        tools_list: list[str] = []
        for tool_spec in tool_allowlist:
            # Each tool_spec might be comma-separated
            tools_list.extend(t.strip() for t in tool_spec.split(",") if t.strip())

        # Check if any tool starts with '+' (additive syntax)
        additive_mode = any(t.startswith("+") for t in tools_list)

        if additive_mode:
            # Strip '+' prefix from all tools
            additional_tools = [t[1:] if t.startswith("+") else t for t in tools_list]
            # Filter out empty strings (e.g., from '+' alone)
            additional_tools = [t for t in additional_tools if t]

            if additional_tools:
                # Prefix with '+' to signal additive mode to config layer
                tool_allowlist_str = "+" + ",".join(additional_tools)
            else:
                # Just '+' means use defaults
                tool_allowlist_str = None
        else:
            # Normal mode - replace defaults with specified tools
            tool_allowlist_str = ",".join(tools_list) if tools_list else None
    else:
        # User explicitly provided empty list (e.g., no -t flags with multiple=True)
        tool_allowlist_str = None

    if profile:
        import cProfile
        import pstats
        from datetime import datetime

        print("Profiling enabled...")
        pr = cProfile.Profile()
        pr.enable()

        profile_dir = Path("profiles")
        profile_dir.mkdir(exist_ok=True)
        profile_path = (
            profile_dir / f"gptme-{datetime.now().strftime('%Y%m%d-%H%M%S')}.prof"
        )

        def save_profile():
            pr.disable()
            pr.dump_stats(profile_path)
            print(f"\nProfile saved to {profile_path}")
            print(f"View with: snakeviz {profile_path}")

            # Print top 20 functions
            stats = pstats.Stats(pr)
            stats.sort_stats("cumulative")
            print("\nTop 20 functions by cumulative time:")
            stats.print_stats(20)

        atexit.register(save_profile)

    interactive = not non_interactive
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
        else:
            # If stdin is not a tty and we have prompts provided as arguments,
            # automatically switch to non-interactive mode to avoid termios errors
            if prompts:
                logger.info(
                    "stdin is not a TTY and prompts provided, switching to non-interactive mode"
                )
                interactive = False
                no_confirm = True

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

    # Register atexit handler to show conversation ID on exit
    def goodbye_handler():
        print(f"\nGoodbye! (resume with: gptme --name {logdir.name})")

    atexit.register(goodbye_handler)

    if workspace == "@log":
        workspace_path: Path | None = logdir / "workspace"
        assert workspace_path  # mypy not smart enough to see its not None
        workspace_path.mkdir(parents=True, exist_ok=True)
    else:
        workspace_path = Path(workspace) if workspace else Path.cwd()

    # Setup complete configuration from CLI arguments and workspace
    config = setup_config_from_cli(
        workspace=workspace_path,
        logdir=logdir,
        model=model,
        tool_allowlist=tool_allowlist_str,
        tool_format=tool_format,
        stream=stream,
        interactive=interactive,
        agent_path=Path(agent_path) if agent_path else None,
    )
    assert config.chat and config.chat.tool_format

    # init telemetry with agent name and interactive mode
    agent_config = config.chat.agent_config
    agent_name = agent_config.name if agent_config else None
    init_telemetry(
        service_name="gptme-cli",
        agent_name=agent_name,
        interactive=interactive,
    )

    # early init tools to generate system prompt
    # We pass the tool_allowlist CLI argument. If it's not provided, init_tools
    # will load it from the environment variable TOOL_ALLOWLIST or the chat config.
    logger.debug(f"Using tools: {config.chat.tools}")
    tools = init_tools(config.chat.tools)

    # Check if we're resuming an existing conversation
    # If so, skip generating initial messages (including expensive context_cmd)
    # as they're already in the loaded log
    log_file = logdir / "conversation.jsonl"
    is_existing_conversation = (
        resume and log_file.exists() and log_file.stat().st_size > 0
    )

    if is_existing_conversation:
        logger.debug(
            "Resuming existing conversation, skipping initial prompt generation"
        )
        initial_msgs = []
    else:
        # get initial system prompt
        initial_msgs = get_prompt(
            tools=tools,
            prompt=prompt_system,
            interactive=config.chat.interactive,
            tool_format=config.chat.tool_format,
            model=config.chat.model,
            workspace=workspace_path,
            agent_path=config.chat.agent,
        )

    # register a handler for Ctrl-C
    set_interruptible()  # prepare, user should be able to Ctrl+C until user prompt ready
    signal.signal(signal.SIGINT, handle_keyboard_interrupt)

    try:
        chat(
            prompt_msgs,
            initial_msgs,
            logdir,
            config.chat.workspace,
            config.chat.model,
            config.chat.stream,
            no_confirm,
            config.chat.interactive,
            show_hidden,
            config.chat.tools,
            config.chat.tool_format,
        )
    except RuntimeError as e:
        if verbose:
            logger.exception(e)
        else:
            logger.error(e)
        sys.exit(1)
    finally:
        shutdown_telemetry()


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
        return get_logdir(convs[index - 1].id)


def get_logdir(logdir: Path | str | Literal["random"]) -> Path:
    logs_dir = get_logs_dir()
    if logdir == "random":
        logdir = logs_dir / generate_conversation_id(name="random", logs_dir=logs_dir)
    elif isinstance(logdir, str):
        logdir = logs_dir / logdir

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
