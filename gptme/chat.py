import logging
import os
import sys
import termios
from collections.abc import Generator
from pathlib import Path

from .commands import execute_cmd
from .config import get_config
from .constants import INTERRUPT_CONTENT, PROMPT_USER
from .init import init
from .llm import reply
from .llm.models import get_default_model, get_model
from .logmanager import Log, LogManager, prepare_messages
from .message import Message
from .telemetry import trace_function
from .tools import (
    ConfirmFunc,
    ToolFormat,
    ToolUse,
    execute_msg,
    get_tools,
    has_tool,
    set_tool_format,
)
from .tools.tts import speak, stop, tts_request_queue
from .util import console, path_with_tilde
from .util.ask_execute import ask_execute
from .util.context import autocommit, include_paths, run_precommit_checks
from .util.cost import log_costs
from .util.interrupt import clear_interruptible, set_interruptible
from .util.prompt import add_history, get_input
from .util.sound import print_bell, wait_for_audio
from .util.terminal import set_current_conv_name, terminal_state_title

logger = logging.getLogger(__name__)

# Global flag to track if we were recently interrupted
_recently_interrupted = False


@trace_function(name="chat.main", attributes={"component": "chat"})
def chat(
    prompt_msgs: list[Message],
    initial_msgs: list[Message],
    logdir: Path,
    workspace: Path,
    model: str | None,
    stream: bool = True,
    no_confirm: bool = False,
    interactive: bool = True,
    show_hidden: bool = False,
    tool_allowlist: list[str] | None = None,
    tool_format: ToolFormat | None = None,
) -> None:
    """
    Run the chat loop.

    prompt_msgs: list of messages to execute in sequence.
    initial_msgs: list of history messages.
    workspace: path to workspace directory.

    Callable from other modules.
    """
    global _recently_interrupted
    _recently_interrupted = False

    # Set initial terminal title with conversation name
    conv_name = logdir.name
    set_current_conv_name(conv_name)

    # init
    init(model, interactive, tool_allowlist)

    default_model = get_default_model()
    assert default_model is not None, "No model loaded and no model specified"
    modelmeta = get_model(model or default_model.full)
    if not modelmeta.supports_streaming and stream:
        logger.info(
            "Disabled streaming for '%s/%s' model (not supported)",
            modelmeta.provider,
            modelmeta.model,
        )
        stream = False

    console.log(f"Using logdir: {path_with_tilde(logdir)}")
    manager = LogManager.load(logdir, initial_msgs=initial_msgs, create=True)

    # tool_format should already be resolved by this point
    assert (
        tool_format is not None
    ), "tool_format should be resolved before calling chat()"

    # By defining the tool_format at the last moment we ensure we can use the
    # configuration for subagent
    set_tool_format(tool_format)

    # Initialize workspace
    console.log(f"Using workspace: {path_with_tilde(workspace)}")
    os.chdir(workspace)

    # print log
    manager.log.print(show_hidden=show_hidden)
    console.print("--- ^^^ past messages ^^^ ---")

    def confirm_func(msg) -> bool:
        if no_confirm:
            return True
        return ask_execute(msg)

    # Convert prompt_msgs to a queue for unified handling
    prompt_queue = list(prompt_msgs)

    # main loop
    while True:
        msg: Message | None = None
        try:
            # Process next message (either from prompt queue or user input)
            if prompt_queue:
                msg = prompt_queue.pop(0)
                msg = include_paths(msg, workspace)
                manager.append(msg)

                # Handle user commands
                if msg.role == "user" and execute_cmd(msg, manager, confirm_func):
                    continue

                # Process the message and get response
                _process_message_conversation(
                    manager, stream, confirm_func, tool_format, workspace, model
                )
            else:
                # Get user input or exit if non-interactive
                if not interactive:
                    logger.debug("Non-interactive and exhausted prompts")
                    _wait_for_tts_if_enabled()
                    break

                msg = _get_user_input(manager.log, workspace)
                if msg is None:
                    # Either user wants to exit OR we should generate response directly
                    if _should_prompt_for_input(manager.log):
                        # User wants to exit
                        break
                    else:
                        # Don't prompt for input, generate response directly (crash recovery, etc.)
                        # Process existing log without adding new message
                        _process_message_conversation(
                            manager,
                            stream,
                            confirm_func,
                            tool_format,
                            workspace,
                            model,
                        )
                else:
                    # Normal case: user provided input
                    manager.append(msg)

                    # Reset interrupt flag since user provided new input
                    _recently_interrupted = False

                    # Handle user commands
                    if msg.role == "user" and execute_cmd(msg, manager, confirm_func):
                        continue

                    # Process the message and get response
                    _process_message_conversation(
                        manager,
                        stream,
                        confirm_func,
                        tool_format,
                        workspace,
                        model,
                    )

        except KeyboardInterrupt:
            console.log("Interrupted.")
            _recently_interrupted = True
            manager.append(Message("system", INTERRUPT_CONTENT))
            # Clear any remaining prompts to avoid confusion
            prompt_queue.clear()
            continue


def _process_message_conversation(
    manager: LogManager,
    stream: bool,
    confirm_func: ConfirmFunc,
    tool_format: ToolFormat,
    workspace: Path,
    model: str | None,
) -> None:
    """Process a message and generate responses until no more tools to run."""
    while True:
        try:
            set_interruptible()
            response_msgs = list(
                step(
                    manager.log,
                    stream,
                    confirm_func,
                    tool_format=tool_format,
                    workspace=workspace,
                    model=model,
                )
            )
        except KeyboardInterrupt:
            console.log("Interrupted during response generation.")
            global _recently_interrupted
            _recently_interrupted = True
            manager.append(Message("system", INTERRUPT_CONTENT))
            break
        finally:
            clear_interruptible()

        for response_msg in response_msgs:
            manager.append(response_msg)
            # run any user-commands, if msg is from user
            if response_msg.role == "user" and execute_cmd(
                response_msg, manager, confirm_func
            ):
                return

        # Check if there are any runnable tools left
        last_content = next(
            (m.content for m in reversed(manager.log) if m.role == "assistant"),
            "",
        )
        has_runnable = any(
            tooluse.is_runnable for tooluse in ToolUse.iter_from_content(last_content)
        )
        if not has_runnable:
            break

    # After all tools are executed, check for modifications and run autocommit/pre-commit
    _check_and_handle_modifications(manager)


def _check_and_handle_modifications(manager: LogManager) -> None:
    """Check for modifications and handle autocommit/pre-commit after conversation is done."""
    global _recently_interrupted

    # Skip automatic actions if we were recently interrupted
    if _recently_interrupted:
        return

    if check_for_modifications(manager.log):
        try:
            set_interruptible()

            success, failed_check_message = check_changes()
            if success:
                if get_config().get_env_bool("GPTME_AUTOCOMMIT"):
                    autocommit_msg = autocommit()
                    manager.append(autocommit_msg)
                    return
            elif failed_check_message:
                manager.append(Message("system", failed_check_message, quiet=False))
                return

        except KeyboardInterrupt:
            console.log("Interrupted during pre-commit/autocommit.")
            _recently_interrupted = True
        finally:
            clear_interruptible()


def _should_prompt_for_input(log: Log) -> bool:
    """
    Determine if we should ask for user input or generate response directly.

    Returns True if we should prompt for input, False if we should generate response.
    This preserves the original logic for handling edge cases like crash recovery.
    """
    last_msg = log[-1] if log else None

    # Ask for input when:
    # - No messages at all
    # - Last message was from assistant (normal flow)
    # - Last message was an interrupt
    # - Last message was pinned
    # - No user messages exist in the entire log
    return (
        not last_msg
        or (last_msg.role in ["assistant"])
        or last_msg.content == INTERRUPT_CONTENT
        or last_msg.pinned
        or not any(role == "user" for role in [m.role for m in log])
    )


def _get_user_input(log: Log, workspace: Path | None) -> Message | None:
    """Get user input, returning None if user wants to exit."""
    clear_interruptible()  # Don't interrupt during user input

    # Check if we should prompt for input or generate response directly
    if not _should_prompt_for_input(log):
        # Last message was from user (crash recovery, edited log, etc.)
        # Don't ask for input, let the system generate a response
        return None

    # print diff between now and last user message timestamp
    if get_config().get_env_bool("GPTME_SHOW_WORKED"):
        last_user_msg = next((m for m in reversed(log) if m.role == "user"), None)
        if last_user_msg and log:
            diff = log[-1].timestamp - last_user_msg.timestamp
            console.log(f"Worked for {diff.total_seconds():.2f} seconds")

    try:
        inquiry = prompt_user()
        msg = Message("user", inquiry, quiet=True)
        msg = include_paths(msg, workspace)
        return msg
    except (EOFError, KeyboardInterrupt):
        return None


def _wait_for_tts_if_enabled() -> None:
    """Wait for TTS to finish if enabled."""
    if has_tool("tts") and os.environ.get("GPTME_VOICE_FINISH", "").lower() in [
        "1",
        "true",
    ]:
        logger.info("Waiting for TTS to finish...")
        set_interruptible()
        try:
            # Wait for all TTS processing to complete
            tts_request_queue.join()
            logger.info("tts request queue joined")
            # Then wait for all audio to finish playing
            wait_for_audio()
            logger.info("audio playback finished")
        except KeyboardInterrupt:
            logger.info("Interrupted while waiting for TTS")
            stop()


@trace_function(name="chat.step", attributes={"component": "chat"})
def step(
    log: Log | list[Message],
    stream: bool,
    confirm: ConfirmFunc,
    tool_format: ToolFormat = "markdown",
    workspace: Path | None = None,
    model: str | None = None,
) -> Generator[Message, None, None]:
    """Runs a single pass of the chat - generates response and executes tools."""
    global _recently_interrupted

    default_model = get_default_model()
    assert default_model is not None, "No model loaded and no model specified"
    model = model or default_model.full
    if isinstance(log, list):
        log = Log(log)

    # Generate response and run tools
    try:
        set_interruptible()

        # performs reduction/context trimming, if necessary
        msgs = prepare_messages(log.messages, workspace)

        tools = None
        if tool_format == "tool":
            tools = [t for t in get_tools() if t.is_runnable]

        # generate response
        with terminal_state_title("🤔 generating"):
            msg_response = reply(msgs, get_model(model).full, stream, tools)
            if get_config().get_env_bool("GPTME_COSTS"):
                log_costs(msgs + [msg_response])

        # speak if TTS tool is available
        if has_tool("tts"):
            speak(msg_response.content)

        # log response and run tools
        if msg_response:
            yield msg_response.replace(quiet=True)
            yield from execute_msg(msg_response, confirm)

        # Reset interrupt flag after successful completion
        _recently_interrupted = False

    finally:
        clear_interruptible()


def prompt_user(value=None) -> str:  # pragma: no cover
    print_bell()
    # Flush stdin to clear any buffered input before prompting (only if stdin is a TTY)
    if sys.stdin.isatty():
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    response = ""
    with terminal_state_title("⌨️ waiting for input"):
        while not response:
            try:
                set_interruptible()
                response = prompt_input(PROMPT_USER, value)
                if response:
                    add_history(response)
            except KeyboardInterrupt:
                print("\nInterrupted. Press Ctrl-D to exit.")
            except EOFError:
                print("\nGoodbye!")
                sys.exit(0)
    clear_interruptible()
    return response


def prompt_input(prompt: str, value=None) -> str:  # pragma: no cover
    """Get input using prompt_toolkit with fish-style suggestions."""
    prompt = prompt.strip() + ": "
    if value:
        console.print(prompt + value)
        return value

    return get_input(prompt)


def check_for_modifications(log: Log) -> bool:
    """Check if there are any file modifications in last 3 messages or since last user message."""
    messages_since_user = []
    found_user_message = False

    for m in reversed(log):
        if m.role == "user":
            found_user_message = True
            break
        messages_since_user.append(m)

    # If no user message found, skip the check (only system messages so far)
    if not found_user_message:
        return False

    # FIXME: this is hacky and unreliable
    has_modifications = any(
        tu.tool in ["save", "patch", "append", "morph"]
        for m in messages_since_user[:3]
        for tu in ToolUse.iter_from_content(m.content)
    )
    # logger.debug(
    #     f"Found {len(messages_since_user)} messages since user ({found_user_message=}, {has_modifications=})"
    # )
    return has_modifications


def check_changes() -> tuple[bool, str | None]:
    """Run lint/pre-commit checks after file modifications."""
    return run_precommit_checks()
