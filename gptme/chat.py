import logging
import os
import sys
import termios
import threading
from collections.abc import Generator
from pathlib import Path

from .commands import execute_cmd
from .config import ChatConfig, get_config
from .constants import (
    DECLINED_CONTENT,
    INTERRUPT_CONTENT,
    MAX_MESSAGE_LENGTH,
    MAX_PROMPT_QUEUE_SIZE,
    PROMPT_USER,
)
from .hooks import HookType, trigger_hook
from .init import init
from .llm import reply
from .llm.models import get_default_model, get_model
from .logmanager import Log, LogManager, prepare_messages
from .message import Message
from .telemetry import set_conversation_context, trace_function
from .tools import (
    ConfirmFunc,
    ToolFormat,
    ToolUse,
    execute_msg,
    get_tools,
)
from .tools.complete import SessionCompleteException
from .util import console, path_with_tilde
from .util.ask_execute import ask_execute
from .util.auto_naming import auto_generate_display_name
from .util.context import include_paths
from .util.cost import log_costs
from .util.input_collector import InputCollector
from .util.interrupt import clear_interruptible, set_interruptible
from .util.prompt import add_history, get_input
from .util.sound import print_bell
from .util.terminal import set_current_conv_name, terminal_state_title

logger = logging.getLogger(__name__)


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
    output_schema: type | None = None,
) -> None:
    """
    Run the chat loop.

    prompt_msgs: list of messages to execute in sequence.
    initial_msgs: list of history messages.
    workspace: path to workspace directory.

    Callable from other modules.
    """
    # Set initial terminal title with conversation name
    conv_name = logdir.name
    set_current_conv_name(conv_name)

    # Set conversation context for telemetry
    # This propagates to all spans in this conversation
    set_conversation_context(conversation_id=conv_name)

    # tool_format should already be resolved by this point
    assert (
        tool_format is not None
    ), "tool_format should be resolved before calling chat()"

    # init
    init(model, interactive, tool_allowlist, tool_format)

    # Trigger session start hooks
    if session_start_msgs := trigger_hook(
        HookType.SESSION_START,
        logdir=logdir,
        workspace=workspace,
        initial_msgs=initial_msgs,
    ):
        # Process any messages from session start hooks
        for hook_msg in session_start_msgs:
            initial_msgs = initial_msgs + [hook_msg]

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

    # Note: todowrite replay is now handled by the todo_replay tool via SESSION_START hook

    # Initialize workspace
    console.log(f"Using workspace: {path_with_tilde(workspace)}")
    os.chdir(workspace)

    # print log
    manager.log.print(show_hidden=show_hidden)
    console.print("--- ^^^ past messages ^^^ ---")

    # Note: todowrite replay is now handled by the todo_replay tool via SESSION_START hook

    def confirm_func(msg) -> bool:
        if no_confirm:
            return True
        return ask_execute(msg)

    # Convert prompt_msgs to a queue for unified handling
    prompt_queue = list(prompt_msgs)

    # Import SessionCompleteException for clean exit handling

    # main loop
    try:
        _run_chat_loop(
            manager,
            prompt_queue,
            stream,
            confirm_func,
            tool_format,
            None,  # Pass None to allow dynamic model switching via /model command
            interactive,
            logdir,
            output_schema,
        )
    except SessionCompleteException as e:
        console.log(f"Autonomous mode: {e}. Exiting.")

        # Trigger session end hooks
        if session_end_msgs := trigger_hook(
            HookType.SESSION_END, logdir=logdir, manager=manager
        ):
            for msg in session_end_msgs:
                manager.append(msg)
        return


def _run_chat_loop(
    manager,
    prompt_queue,
    stream,
    confirm_func,
    tool_format,
    model,
    interactive,
    logdir,
    output_schema=None,
):
    """Main chat loop - extracted to allow clean exception handling."""

    # Create input collector for type-ahead support
    def on_input_queued(queue_size: int) -> None:
        """Callback when input is queued."""
        from .util import console

        console.print(f"[dim][{queue_size} prompt(s) queued][/dim]", end="\r")

    input_collector = (
        InputCollector(
            prompt_queue,
            max_queue_size=MAX_PROMPT_QUEUE_SIZE,
            on_input_queued=on_input_queued if interactive else None,
        )
        if interactive
        else None
    )

    while True:
        msg: Message | None = None
        try:
            # Process next message (either from prompt queue or user input)
            if prompt_queue:
                msg = prompt_queue.pop(0)
                assert msg is not None, "prompt_queue contained None"
                msg = include_paths(msg, manager.workspace)
                manager.append(msg)

                # Handle user commands
                if msg.role == "user" and execute_cmd(msg, manager, confirm_func):
                    continue

                # Process the message and get response
                if input_collector:
                    input_collector.start()
                _process_message_conversation(
                    manager, stream, confirm_func, tool_format, model, output_schema
                )
                if input_collector:
                    input_collector.stop()
            else:
                # Get user input or exit if non-interactive
                if not interactive:
                    logger.debug("Non-interactive and exhausted prompts")
                    break

                # Stop input collector before prompting
                if input_collector:
                    input_collector.stop()
                # Don't flush stdin when using input collector - preserve typed-ahead input
                user_input = _get_user_input(
                    manager.log,
                    manager.workspace,
                    flush_stdin=input_collector is None,
                )
                if user_input is None:
                    # Either user wants to exit OR we should generate response directly
                    if _should_prompt_for_input(manager.log):
                        # User wants to exit
                        break
                    else:
                        # Don't prompt for input, generate response directly (crash recovery, etc.)
                        # Process existing log without adding new message
                        if input_collector:
                            input_collector.start()
                        _process_message_conversation(
                            manager,
                            stream,
                            confirm_func,
                            tool_format,
                            model,
                            output_schema,
                        )
                        if input_collector:
                            input_collector.stop()
                else:
                    # Normal case: user provided input
                    msg = user_input
                    manager.append(msg)

                    # Reset interrupt flag since user provided new input

                    # Handle user commands
                    if msg.role == "user" and execute_cmd(msg, manager, confirm_func):
                        continue

                    # Process the message and get response
                    if input_collector:
                        input_collector.start()
                    _process_message_conversation(
                        manager,
                        stream,
                        confirm_func,
                        tool_format,
                        model,
                        output_schema,
                    )
                    if input_collector:
                        input_collector.stop()

            # Trigger LOOP_CONTINUE hooks to check if we should continue/exit
            # This handles auto-reply mechanism and other loop control logic
            if loop_msgs := trigger_hook(
                HookType.LOOP_CONTINUE,
                manager=manager,
                interactive=interactive,
                prompt_queue=prompt_queue,
            ):
                for msg in loop_msgs:
                    # Add hook-generated messages to prompt queue with size limit
                    if len(prompt_queue) >= MAX_PROMPT_QUEUE_SIZE:
                        logger.warning(
                            f"Prompt queue limit ({MAX_PROMPT_QUEUE_SIZE}) reached, "
                            "dropping message from hook"
                        )
                        break
                    prompt_queue.append(msg)
                    console.log(f"[Loop control] {msg.content[:100]}...")
                continue  # Process the queued messages

        except KeyboardInterrupt:
            console.log("Interrupted.")
            manager.append(Message("system", INTERRUPT_CONTENT))
            # Clear any remaining prompts to avoid confusion
            prompt_queue.clear()
            continue

    # Trigger session end hooks when exiting normally
    if session_end_msgs := trigger_hook(
        HookType.SESSION_END, logdir=logdir, manager=manager
    ):
        for msg in session_end_msgs:
            manager.append(msg)


def _process_message_conversation(
    manager: LogManager,
    stream: bool,
    confirm_func: ConfirmFunc,
    tool_format: ToolFormat,
    model: str | None,
    output_schema: type | None = None,
) -> None:
    """Process a message and generate responses until no more tools to run."""

    while True:
        try:
            set_interruptible()

            # Trigger pre-process hooks (step.pre - before each step in a turn)
            if pre_msgs := trigger_hook(
                HookType.STEP_PRE,
                manager=manager,
            ):
                for msg in pre_msgs:
                    manager.append(msg)

            response_msgs = list(
                step(
                    manager.log,
                    stream,
                    confirm_func,
                    tool_format=tool_format,
                    workspace=manager.workspace,
                    model=model,
                    output_schema=output_schema,
                )
            )
        except KeyboardInterrupt:
            console.log("Interrupted during response generation.")
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

        # Check if user declined execution - return to prompt without generating response
        # This makes "n" at confirm prompt behave like Ctrl+C (return to user prompt)
        if any(msg.content == DECLINED_CONTENT for msg in response_msgs):
            console.log("Execution declined, returning to prompt.")
            break

        # Auto-generate display name after first assistant response if not already set
        # Runs in background thread to avoid blocking the chat loop
        # TODO: Consider implementing via hook system to streamline with server implementation
        # See: gptme/server/api_v2_sessions.py for server's implementation
        # Try auto-naming on first few assistant messages until we get a name
        # This allows retry when initial context is insufficient
        assistant_messages = [m for m in manager.log.messages if m.role == "assistant"]
        if len(assistant_messages) <= 3:
            chat_config = ChatConfig.from_logdir(manager.logdir)
            if not chat_config.name:

                def _auto_name_thread(
                    config: ChatConfig,
                    messages: list[Message],
                    model_name: str,
                ):
                    """Background thread for auto-naming to avoid blocking chat loop."""
                    try:
                        display_name = auto_generate_display_name(messages, model_name)
                        if display_name:
                            config.name = display_name
                            config.save()
                            logger.info(
                                f"Auto-generated conversation name: {display_name}"
                            )
                        else:
                            logger.warning("Auto-naming failed")
                    except Exception as e:
                        logger.warning(f"Failed to auto-generate name: {e}")

                # Start naming in background thread (daemon so it doesn't block exit)
                # Get current model dynamically (model param may be None)
                current_model = get_default_model()
                thread = threading.Thread(
                    target=_auto_name_thread,
                    args=(chat_config, manager.log.messages.copy(), current_model),
                    daemon=True,
                )
                thread.start()

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

    # Trigger post-process hooks after message processing completes (turn.post)
    # Note: pre-commit checks and autocommit are now handled by hooks
    if post_msgs := trigger_hook(
        HookType.TURN_POST,
        manager=manager,
    ):
        for msg in post_msgs:
            manager.append(msg)


def _should_prompt_for_input(log: Log) -> bool:
    """
    Determine if we should ask for user input or generate response directly.

    Returns True if we should prompt for input, False if we should generate response.
    This preserves the original logic for handling edge cases like crash recovery.
    """
    last_msg = log[-1] if log else None

    # Check if there's an interrupt or decline message after the last assistant message
    # This handles cases where hooks (like cost_awareness) add messages after the interrupt/decline
    has_recent_interrupt_or_decline = False
    for msg in reversed(log):
        if msg.role == "assistant":
            break
        if msg.content in (INTERRUPT_CONTENT, DECLINED_CONTENT):
            has_recent_interrupt_or_decline = True
            break

    # Ask for input when:
    # - No messages at all
    # - Last message was from assistant (normal flow)
    # - There was an interrupt or decline after the last assistant message
    # - Last message was pinned
    # - No user messages exist in the entire log
    return (
        not last_msg
        or (last_msg.role in ["assistant"])
        or has_recent_interrupt_or_decline
        or last_msg.pinned
        or not any(role == "user" for role in [m.role for m in log])
    )


def _get_user_input(
    log: Log, workspace: Path | None, flush_stdin: bool = True
) -> Message | None:
    """Get user input, returning None if user wants to exit.

    Args:
        log: The conversation log
        workspace: Path to workspace directory
        flush_stdin: Whether to flush stdin before prompting. Set to False
            when prompt queueing is enabled to preserve typed-ahead input.
    """
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
        inquiry = prompt_user(flush_stdin=flush_stdin)
        # Validate message length to prevent unbounded memory usage
        truncation_suffix = "\n\n[Message truncated due to length]"
        if len(inquiry) > MAX_MESSAGE_LENGTH:
            logger.warning(
                f"Message truncated from {len(inquiry)} to {MAX_MESSAGE_LENGTH} chars"
            )
            # Account for suffix length to stay within MAX_MESSAGE_LENGTH
            inquiry = (
                inquiry[: MAX_MESSAGE_LENGTH - len(truncation_suffix)]
                + truncation_suffix
            )
        msg = Message("user", inquiry, quiet=True)
        msg = include_paths(msg, workspace)
        return msg
    except (EOFError, KeyboardInterrupt):
        return None


@trace_function(name="chat.step", attributes={"component": "chat"})
def step(
    log: Log | list[Message],
    stream: bool,
    confirm: ConfirmFunc,
    tool_format: ToolFormat = "markdown",
    workspace: Path | None = None,
    model: str | None = None,
    output_schema: type | None = None,
) -> Generator[Message, None, None]:
    """Runs a single pass of the chat - generates response and executes tools."""
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
            msg_response = reply(
                msgs, get_model(model).full, stream, tools, workspace, output_schema
            )
            if get_config().get_env_bool("GPTME_COSTS"):
                log_costs(msgs + [msg_response])

        # Trigger generation post hooks (e.g., TTS)
        if generation_post_msgs := trigger_hook(
            HookType.GENERATION_POST,
            message=msg_response,
            workspace=workspace,
        ):
            for msg in generation_post_msgs:
                logger.debug(f"Generation post hook yielded: {msg}")

        # log response and run tools
        if msg_response:
            yield msg_response.replace(quiet=True)
            yield from execute_msg(msg_response, confirm, log, workspace)

    finally:
        clear_interruptible()


def prompt_user(value=None, flush_stdin: bool = True) -> str:  # pragma: no cover
    print_bell()
    # Flush stdin to clear any buffered input before prompting (only if stdin is a TTY)
    # When prompt queueing is enabled (flush_stdin=False), we preserve typed-ahead input
    if flush_stdin and sys.stdin.isatty():
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
