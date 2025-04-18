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
from pathlib import Path

from .chat import init, step
from .config import get_config
from .logmanager import LogManager
from .message import Message, RoleLiteral
from .tools import ToolUse
from .util.ask_execute import ask_execute

logger = logging.getLogger(__name__)


@dataclass
class MessageComponent:
    """Wrapper for Message that includes display state."""

    message: Message
    expanded: bool = False
    selected: bool = False

    def __eq__(self, other):
        if not isinstance(other, MessageComponent):
            return False
        return self.message == other.message


class MessageApp:
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

        # Initialize logging handler
        self.log_messages: list[tuple[int, str]] = []
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

        class CursesHandler(logging.Handler):
            def __init__(self, app):
                super().__init__()
                self.app = app

            def emit(self, record):
                self.app.log_messages.append((record.levelno, self.format(record)))
                self.app.draw()  # Redraw to show new log message

        handler = CursesHandler(self)
        handler.setLevel(log_level)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)

    def _init_message_components(self) -> list[MessageComponent]:
        """Initialize message components from conversation log."""
        if not self.manager:
            return []
        return [MessageComponent(msg) for msg in self.manager.log]

    def add_message(self, content: str) -> None:
        """Add a new message to the conversation."""
        msg = Message(self.current_role, content)
        if self.manager:
            self.manager.append(msg)
        self.message_components.append(MessageComponent(msg))

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
        self._draw_log_messages(height, width)
        self._draw_input_box(height, width)
        self._draw_mode_indicator(width)
        self._position_cursor(height, width)

        self.stdscr.refresh()

    def _draw_messages(self, height: int, width: int) -> None:
        """Draw conversation messages."""
        current_y = 0
        for msg_comp in self.message_components[self.scroll_offset :]:
            if current_y >= height - 5:  # Leave space for input and log messages
                break
            lines_used = self._draw_single_message(current_y, msg_comp, width)
            current_y += lines_used

    def _draw_single_message(
        self, y: int, msg_comp: MessageComponent, width: int
    ) -> int:
        """Draw a single message and return number of lines used."""
        if msg_comp == self.selected_message:
            self.stdscr.attron(curses.A_REVERSE)

        role_color = _role_color(msg_comp.message.role) if self.use_color else None
        if role_color:
            self.stdscr.attron(curses.color_pair(role_color))
        self.stdscr.addstr(y, 1, f"[{msg_comp.message.role}] ")
        if role_color:
            self.stdscr.attroff(curses.color_pair(role_color))

        content = msg_comp.message.content
        wrapped_lines = textwrap.wrap(content, width - 12)
        lines_to_show = wrapped_lines if msg_comp.expanded else wrapped_lines[:3]

        for i, line in enumerate(lines_to_show):
            if y + i >= self.stdscr.getmaxyx()[0] - 5:
                break
            self.stdscr.addstr(y + i, 11, line)

        if not msg_comp.expanded and len(wrapped_lines) > 3:
            if y + 2 < self.stdscr.getmaxyx()[0] - 5:
                self.stdscr.addstr(y + 2, width - 5, "...")

        if msg_comp == self.selected_message:
            self.stdscr.attroff(curses.A_REVERSE)

        return len(lines_to_show)

    def _draw_log_messages(self, height: int, width: int) -> None:
        """Draw log messages at the bottom of the screen."""
        start_y = height - 4
        for i, (level, msg) in enumerate(
            self.log_messages[-2:]
        ):  # Show last 2 log messages
            color = (
                curses.COLOR_RED
                if level >= logging.ERROR
                else (
                    curses.COLOR_YELLOW
                    if level >= logging.WARNING
                    else curses.COLOR_GREEN
                )
            )
            if self.use_color:
                self.stdscr.attron(curses.color_pair(color))
            self.stdscr.addstr(start_y + i, 0, msg[: width - 1])
            if self.use_color:
                self.stdscr.attroff(curses.color_pair(color))

    def _draw_input_box(self, height: int, width: int) -> None:
        """Draw the input box."""
        self.stdscr.addstr(height - 2, 0, "-" * width)
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
            return True
        elif key == ord("i"):
            self.mode = "input"
            self.cursor_x = len(self.input_buffer)
        elif key == ord("s"):
            self.mode = "select"
            self.selected_message = (
                MessageComponent(self.message_components[0].message)
                if self.message_components
                else None
            )
        elif key == ord("r"):
            self.mode = "role"
        elif key == curses.KEY_UP:
            self.scroll_offset = max(0, self.scroll_offset - 1)
        elif key == curses.KEY_DOWN:
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
            key in (curses.KEY_UP, curses.KEY_DOWN)
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
        if key == curses.KEY_UP:
            self.selected_message = MessageComponent(
                self.message_components[max(0, idx - 1)].message
            )
        elif key == curses.KEY_DOWN:
            self.selected_message = MessageComponent(
                self.message_components[
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
    app.run()


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
