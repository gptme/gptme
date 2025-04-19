"""
Ncurses UI for gptme.

Features:
- [x] Load and display existing conversations
- [x] Submit new prompts and step conversations
- [x] Merge logging messages with conversation
- [x] Message expansion/collapse
- [x] Message selection and editing
- [x] Color support
"""

import argparse
import curses
import logging
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .chat import init, step
from .config import get_config
from .logmanager import LogManager
from .message import Message, RoleLiteral
from .tools import ToolUse
from .util.ask_execute import ask_execute

logger = logging.getLogger(__name__)

INPUT_HEIGHT = 2


@dataclass
class MessageComponent:
    """Wrapper for Message that includes display state."""

    message: Message
    expanded: bool = True
    selected: bool = False
    is_log: bool = False  # Whether this is a log message
    log_level: int | None = None  # Log level if this is a log message

    def __eq__(self, other):
        if not isinstance(other, MessageComponent):
            return False
        return self.message == other.message

    @classmethod
    def from_message(cls, message: Message) -> "MessageComponent":
        """Create a MessageComponent from a Message."""
        return cls(message=message, expanded=message.role != "system")

    @classmethod
    def from_log(cls, timestamp: float, level: int, content: str) -> "MessageComponent":
        """Create a MessageComponent from a log message."""

        msg = Message(
            role="system", content=content, timestamp=datetime.fromtimestamp(timestamp)
        )
        return cls(
            message=msg,
            is_log=True,
            log_level=level,
        )


class MessageApp:
    message_components: list[MessageComponent]

    def __init__(
        self,
        stdscr,
        logdir: Path | None = None,
        use_color: bool = True,
        log_level: int = logging.INFO,
    ):
        self.stdscr = stdscr
        self.input_buffer: str = ""
        self.cursor_y: int = 0
        self.cursor_x: int = 0
        self.scroll_offset: int = 0
        self.mode: str = "normal"
        self.selected_message: MessageComponent | None = None
        self.current_role: RoleLiteral = "user"
        self.use_color: bool = curses.has_colors() and use_color

        self.message_components = []

        # Initialize logging handler
        # timestamp, level, message
        self.log_messages: list[tuple[float, int, str]] = []
        self._setup_logging(log_level)

        # Initialize gptme
        config = get_config()
        model = config.get_env("MODEL")
        init(model, interactive=True, tool_allowlist=None)

        # Initialize conversation manager
        self.manager = LogManager.load(logdir) if logdir else None
        self.message_components = self._init_message_components()

    def _setup_logging(self, log_level: int) -> None:
        """Setup logging handler to capture log messages."""
        print("Setting up logging...", flush=True)  # Debug print

        class CursesHandler(logging.Handler):
            def __init__(self, app):
                super().__init__()
                self.app = app

            def emit(self, record):
                msg = (record.created, record.levelno, self.format(record))
                print(f"Log received: {msg}", flush=True)  # Debug print
                self.app.log_messages.append(msg)
                # Add new log message component
                self.app.message_components.append(MessageComponent.from_log(*msg))
                # Sort by timestamp to maintain chronological order
                self.app.message_components.sort(key=lambda x: x.message.timestamp)
                self.app.draw()  # Redraw to show new log message

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)

        # Remove any existing handlers to avoid duplicates
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Add our curses handler
        handler = CursesHandler(self)
        handler.setLevel(log_level)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

        # Test log message
        logger.info("Logging system initialized")
        print("Logging setup complete", flush=True)  # Debug print

    def _init_message_components(self) -> list[MessageComponent]:
        """Initialize message components from conversation log and log messages."""
        components: list[MessageComponent] = []

        # Add conversation messages
        if self.manager:
            components.extend(
                MessageComponent.from_message(msg) for msg in self.manager.log
            )

        # Add log messages
        components.extend(
            MessageComponent.from_log(timestamp, level, msg)
            for timestamp, level, msg in self.log_messages
        )

        # Sort by timestamp
        # NOTE: workspace context message ends up after user message with this enabled
        # components.sort(key=lambda x: x.message.timestamp)
        return components

    def add_message(self, content: str) -> None:
        """Add a new message to the conversation."""
        msg = Message(self.current_role, content)
        if self.manager:
            self.manager.append(msg)

        # Add new message component and resort the list
        self.message_components.append(MessageComponent.from_message(msg))
        self.message_components.sort(key=lambda x: x.message.timestamp)

    def step_conversation(self) -> None:
        """Step the conversation forward."""
        if not self.manager:
            return

        def confirm_func(msg: str) -> bool:
            return ask_execute(msg)

        try:
            # Generate and execute response
            for response_msg in step(
                self.manager.log,
                stream=True,
                confirm=confirm_func,
            ):
                self.manager.append(response_msg)
                self.message_components.append(MessageComponent(response_msg))
                self.draw()

                # Check if there are any runnable tools left
                last_content = response_msg.content
                has_runnable = any(
                    tooluse.is_runnable
                    for tooluse in ToolUse.iter_from_content(last_content)
                )
                if not has_runnable:
                    break

        except Exception as e:
            logger.error(f"Error stepping conversation: {e}")

    def draw(self) -> None:
        """Draw the UI."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        self._draw_messages(height, width)
        self._draw_input_box(height, width)
        self._draw_mode_indicator(width)
        self._position_cursor(height, width)

        self.stdscr.refresh()

    def _draw_messages(self, height: int, width: int) -> None:
        """Draw conversation and log messages."""
        current_y = 0
        for msg_comp in self.message_components[self.scroll_offset :]:
            if current_y >= height - INPUT_HEIGHT:  # Leave space for input box
                break
            lines_used = self._draw_message(current_y, msg_comp, width)
            current_y += lines_used

    def _draw_message(self, y: int, msg_comp: MessageComponent, width: int) -> int:
        """Draw a message (either conversation or log)."""
        # Handle selection highlighting
        if msg_comp == self.selected_message:
            self.stdscr.attron(curses.A_REVERSE)

        # Set up formatting based on message type
        indent_role = 1
        indent_content = 13  # Space for role prefix
        wrap_width = width - indent_content - 1
        if msg_comp.is_log:
            # Log message formatting
            assert msg_comp.log_level is not None
            color = (
                curses.COLOR_RED
                if msg_comp.log_level >= logging.ERROR
                else (
                    curses.COLOR_YELLOW
                    if msg_comp.log_level >= logging.WARNING
                    else curses.COLOR_GREEN
                )
            )

            # For log messages, color only the level indicator
            if self.use_color:
                self.stdscr.attron(curses.color_pair(color))
            level_name = logging.getLevelName(msg_comp.log_level or 0)
            self.stdscr.addstr(y, indent_role, f"{level_name}")
            if self.use_color:
                self.stdscr.attroff(curses.color_pair(color))
        else:
            # Conversation message formatting
            color = _role_color(msg_comp.message.role)
            # Draw role prefix
            if self.use_color:
                self.stdscr.attron(curses.color_pair(color))
            self.stdscr.addstr(y, indent_role, f"[{msg_comp.message.role}] ")
            if self.use_color:
                self.stdscr.attroff(curses.color_pair(color))

        # Handle content wrapping and expansion
        content = msg_comp.message.content

        max_lines_collapsed = 3
        lines = [
            line
            for lines in [
                textwrap.wrap(
                    line,
                    wrap_width,
                    replace_whitespace=False,
                )
                for line in content.split("\n")
            ]
            for line in lines
        ]
        lines_to_show = lines[: (1000 if msg_comp.expanded else max_lines_collapsed)]

        # Show ellipsis on last line for collapsed messages
        if len(lines) > len(lines_to_show):
            lines_to_show[-1] += " ..."

        # Draw content lines (without color)
        for i, line in enumerate(lines_to_show):
            if y + i >= self.stdscr.getmaxyx()[0] - INPUT_HEIGHT - 1:
                break
            self.stdscr.addstr(y + i, indent_content, line)

        # Reset selection highlighting if active
        if msg_comp == self.selected_message:
            self.stdscr.attroff(curses.A_REVERSE)

        return len(lines_to_show)

    def _draw_input_box(self, height: int, width: int) -> None:
        """Draw the input box."""
        self.stdscr.addstr(height - INPUT_HEIGHT, 0, "-" * width)
        role_color = _role_color(self.current_role) if self.use_color else None
        if role_color:
            self.stdscr.attron(curses.color_pair(role_color))
        input_prefix = f"[{self.current_role}]> "
        self.stdscr.addstr(height - 1, 0, input_prefix)
        if role_color:
            self.stdscr.attroff(curses.color_pair(role_color))

        max_input_width = width - len(input_prefix) - 1
        if len(self.input_buffer) > max_input_width:
            visible_input = self.input_buffer[-max_input_width:]
            self.stdscr.addstr(height - 1, len(input_prefix), visible_input)
        else:
            self.stdscr.addstr(height - 1, len(input_prefix), self.input_buffer)

    def _draw_mode_indicator(self, width: int) -> None:
        """Draw the mode indicator."""
        self.stdscr.addstr(0, width - 10, f"[{self.mode.upper()}]")

    def _position_cursor(self, height: int, width: int) -> None:
        """Position the cursor."""
        if self.mode in ["input", "edit"]:
            input_prefix = f"[{self.current_role}]> "
            max_input_width = width - len(input_prefix) - 1
            cursor_x = min(max_input_width, self.cursor_x)
            self.stdscr.move(height - 1, len(input_prefix) + cursor_x)

    def run(self) -> None:
        """Main application loop."""
        self._init_colors()

        while True:
            self.draw()
            key = self.stdscr.getch()

            if self.mode == "normal":
                if self._handle_normal_mode(key):
                    break
            elif self.mode == "input":
                self._handle_input_mode(key)
            elif self.mode == "select":
                self._handle_select_mode(key)
            elif self.mode == "edit":
                self._handle_edit_mode(key)
            elif self.mode == "role":
                self._handle_role_mode(key)

    def _init_colors(self) -> None:
        """Initialize color pairs."""
        curses.curs_set(1)
        if self.use_color:
            curses.start_color()
            curses.init_pair(curses.COLOR_GREEN, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(curses.COLOR_BLUE, curses.COLOR_BLUE, curses.COLOR_BLACK)
            curses.init_pair(curses.COLOR_RED, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(
                curses.COLOR_YELLOW, curses.COLOR_YELLOW, curses.COLOR_BLACK
            )

    def _handle_normal_mode(self, key: int) -> bool:
        """Handle keys in normal mode."""
        if key == ord("q"):
            logger.info("Exiting application")
            return True
        elif key == ord("i"):
            logger.info("Entering input mode")
            self.mode = "input"
            self.cursor_x = len(self.input_buffer)
        elif key == ord("s"):
            logger.info("Entering select mode")
            self.mode = "select"
            self.selected_message = (
                self.message_components[0] if self.message_components else None
            )
        elif key == ord("r"):
            logger.info("Entering role selection mode")
            self.mode = "role"
        elif key == curses.KEY_UP or key == ord("k"):
            self.scroll_offset = max(0, self.scroll_offset - 1)
        elif key == curses.KEY_DOWN or key == ord("j"):
            self.scroll_offset = min(
                len(self.message_components) - 1, self.scroll_offset + 1
            )
        return False

    def _handle_input_mode(self, key: int) -> None:
        """Handle keys in input mode."""
        if key == 27:  # ESC
            self.mode = "normal"
        elif key == 10:  # Enter
            if self.input_buffer:
                self.add_message(self.input_buffer)
                self.input_buffer = ""
                self.cursor_x = 0
                self.step_conversation()
        else:
            self._handle_text_input(key)

    def _handle_select_mode(self, key: int) -> None:
        """Handle keys in select mode."""
        if key == 27:  # ESC
            self.mode = "normal"
            self.selected_message = None
        elif key == ord("e") and self.selected_message is not None:
            self.mode = "edit"
            self.input_buffer = self.selected_message.message.content
            self.cursor_x = len(self.input_buffer)
        elif key == ord("x") and self.selected_message is not None:
            self.selected_message.expanded = not self.selected_message.expanded
        elif key == ord("d") and self.selected_message is not None:
            self._delete_selected_message()
        elif (
            key in (curses.KEY_UP, curses.KEY_DOWN, ord("k"), ord("j"))
            and self.message_components
            and self.selected_message is not None
        ):
            self._move_selection(key)

    def _handle_edit_mode(self, key: int) -> None:
        """Handle keys in edit mode."""
        if key == 27:  # ESC
            self.mode = "select"
            self.input_buffer = ""
            self.cursor_x = 0
        elif key == 10 and self.selected_message is not None:  # Enter
            self.selected_message.message = Message(
                self.selected_message.message.role, self.input_buffer
            )
            if self.manager:
                self.manager.edit(
                    [m.message for m in self.message_components]
                )  # Update log
            self.mode = "select"
            self.input_buffer = ""
            self.cursor_x = 0
        else:
            self._handle_text_input(key)

    def _handle_role_mode(self, key: int) -> None:
        """Handle keys in role mode."""
        if key == ord("u"):
            self.current_role = "user"
            self.mode = "normal"
        elif key == ord("a"):
            self.current_role = "assistant"
            self.mode = "normal"
        elif key == ord("s"):
            self.current_role = "system"
            self.mode = "normal"
        elif key == 27:  # ESC
            self.mode = "normal"

    def _handle_text_input(self, key: int) -> None:
        """Handle text input in input and edit modes."""
        if key == curses.KEY_BACKSPACE or key == 127:
            if self.cursor_x > 0:
                self.input_buffer = (
                    self.input_buffer[: self.cursor_x - 1]
                    + self.input_buffer[self.cursor_x :]
                )
                self.cursor_x -= 1
        elif key == curses.KEY_DC:  # Delete key
            if self.cursor_x < len(self.input_buffer):
                self.input_buffer = (
                    self.input_buffer[: self.cursor_x]
                    + self.input_buffer[self.cursor_x + 1 :]
                )
        elif key == curses.KEY_LEFT:
            self.cursor_x = max(0, self.cursor_x - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor_x = min(len(self.input_buffer), self.cursor_x + 1)
        elif key == curses.KEY_HOME:
            self.cursor_x = 0
        elif key == curses.KEY_END:
            self.cursor_x = len(self.input_buffer)
        elif 32 <= key <= 126:  # Printable ASCII characters
            self.input_buffer = (
                self.input_buffer[: self.cursor_x]
                + chr(key)
                + self.input_buffer[self.cursor_x :]
            )
            self.cursor_x += 1

    def _delete_selected_message(self) -> None:
        """Delete the selected message."""
        assert self.selected_message is not None
        self.message_components.remove(self.selected_message)
        if self.message_components:
            self.selected_message = MessageComponent(self.message_components[0].message)
        else:
            self.selected_message = None
            self.mode = "normal"
        if self.manager:
            self.manager.edit(
                [m.message for m in self.message_components]
            )  # Update log

    def _move_selection(self, key: int) -> None:
        """Move the selection up or down."""
        assert self.selected_message is not None
        idx = self.message_components.index(self.selected_message)
        if key == curses.KEY_UP or key == ord("k"):
            self.selected_message = MessageComponent(
                message=self.message_components[max(0, idx - 1)].message
            )
        elif key == curses.KEY_DOWN or key == ord("j"):
            self.selected_message = MessageComponent(
                message=self.message_components[
                    min(len(self.message_components) - 1, idx + 1)
                ].message
            )


def _role_color(role: str) -> int:
    """Get the color for a role."""
    return (
        curses.COLOR_GREEN
        if role == "user"
        else curses.COLOR_BLUE
        if role == "assistant"
        else curses.COLOR_RED
    )


def _main(stdscr, logdir: Path | None, use_color: bool):
    """Main entry point for the curses application."""
    app = MessageApp(stdscr, logdir, use_color)
    if not app.message_components:
        app.add_message("Welcome to the gptme Message App!")
        app.add_message(
            "Press 'i' to enter input mode, 's' to enter select mode, 'r' to change role, and 'q' to quit."
        )
        app.add_message(
            "In select mode, use arrow keys to navigate, 'e' to edit, 'x' to expand/collapse, and 'd' to delete."
        )
    try:
        app.run()
    except KeyboardInterrupt:
        print("Goodbye!")
        exit(0)


def main():
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="gptme Message App")
    parser.add_argument("--no-color", action="store_true", help="Disable color output")
    parser.add_argument(
        "--logdir", type=Path, help="Path to conversation log directory"
    )
    args = parser.parse_args()

    curses.wrapper(lambda stdscr: _main(stdscr, args.logdir, not args.no_color))


if __name__ == "__main__":
    main()
