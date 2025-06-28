"""
Very WIP ncurses UI for gptme.

- [x] basic chat app
- [ ] gptme integration
"""

import argparse
import curses
import textwrap
import os


class Message:
    def __init__(self, content: str, role: str = "user"):
        self.content: str = content
        self.expanded: bool = False
        self.role: str = role


class MessageApp:
    def __init__(self, stdscr, use_color: bool = True):
        self.stdscr = stdscr
        self.messages: list[Message] = []
        self.input_buffer: str = ""
        self.cursor_y: int = 0
        self.cursor_x: int = 0
        self.scroll_offset: int = 0
        self.mode: str = "normal"
        self.selected_message: Message | None = None
        self.current_role: str = "user"
        self.use_color: bool = curses.has_colors() and use_color

    def add_message(self, content: str) -> None:
        self.messages.append(Message(content, self.current_role))

    def draw(self) -> None:
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        self._draw_messages(height, width)
        self._draw_input_box(height, width)
        self._draw_mode_indicator(width)
        self._position_cursor(height, width)

        self.stdscr.refresh()

    def _draw_messages(self, height: int, width: int) -> None:
        for i, message in enumerate(self.messages[self.scroll_offset :]):
            if i >= height - 3:
                break
            self._draw_single_message(i, message, width)

    def _draw_single_message(self, i: int, message: Message, width: int) -> None:
        if message == self.selected_message:
            self.stdscr.attron(curses.A_REVERSE)

        role_color = _role_color(message.role) if self.use_color else None
        if role_color:
            self.stdscr.attron(curses.color_pair(role_color))
        self.stdscr.addstr(i, 1, f"[{message.role}] ")
        if role_color:
            self.stdscr.attroff(curses.color_pair(role_color))

        wrapped_lines = textwrap.wrap(
            message.content, width - 12
        )  # Adjusted for role prefix
        for j, line in enumerate(wrapped_lines[: 3 if not message.expanded else None]):
            self.stdscr.addstr(i + j, 11, line)  # Adjusted for role prefix
        if not message.expanded and len(wrapped_lines) > 3:
            self.stdscr.addstr(i + 2, width - 5, "...")
        if message == self.selected_message:
            self.stdscr.attroff(curses.A_REVERSE)

    def _draw_input_box(self, height: int, width: int) -> None:
        self.stdscr.addstr(height - 2, 0, "-" * width)
        role_color = _role_color(self.current_role) if self.use_color else None
        if role_color:
            self.stdscr.attron(curses.color_pair(role_color))
        input_prefix = f"[{self.current_role}]> "
        self.stdscr.addstr(height - 1, 0, input_prefix)
        if role_color:
            self.stdscr.attroff(curses.color_pair(role_color))

        max_input_width = width - len(input_prefix) - 1  # Leave 1 character for cursor
        if len(self.input_buffer) > max_input_width:
            visible_input = self.input_buffer[-max_input_width:]
            self.stdscr.addstr(height - 1, len(input_prefix), visible_input)
        else:
            self.stdscr.addstr(height - 1, len(input_prefix), self.input_buffer)

    def _draw_mode_indicator(self, width: int) -> None:
        self.stdscr.addstr(0, width - 10, f"[{self.mode.upper()}]")

    def _position_cursor(self, height: int, width: int) -> None:
        if self.mode == "input" or self.mode == "edit":
            input_prefix = f"[{self.current_role}]> "
            max_input_width = width - len(input_prefix) - 1
            cursor_x = min(max_input_width, self.cursor_x)
            self.stdscr.move(height - 1, len(input_prefix) + cursor_x)

    def run(self) -> None:
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
        curses.curs_set(1)
        if self.use_color:
            curses.start_color()
            curses.init_pair(curses.COLOR_GREEN, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(curses.COLOR_BLUE, curses.COLOR_BLUE, curses.COLOR_BLACK)
            curses.init_pair(curses.COLOR_RED, curses.COLOR_RED, curses.COLOR_BLACK)

    def _handle_normal_mode(self, key: int) -> bool:
        if key == ord("q"):
            return True
        elif key == ord("i"):
            self.mode = "input"
            self.cursor_x = len(self.input_buffer)
        elif key == ord("s"):
            self.mode = "select"
            self.selected_message = self.messages[0] if self.messages else None
        elif key == ord("r"):
            self.mode = "role"
        elif key == curses.KEY_UP:
            self.scroll_offset = max(0, self.scroll_offset - 1)
        elif key == curses.KEY_DOWN:
            self.scroll_offset = min(len(self.messages) - 1, self.scroll_offset + 1)
        return False

    def _handle_input_mode(self, key: int) -> None:
        if key == 27:  # ESC
            self.mode = "normal"
        elif key == 10:  # Enter
            if self.input_buffer:
                self.add_message(self.input_buffer)
                self.input_buffer = ""
                self.cursor_x = 0
        else:
            self._handle_text_input(key)

    def _handle_select_mode(self, key: int) -> None:
        if key == 27:  # ESC
            self.mode = "normal"
            self.selected_message = None
        elif key == ord("e") and self.selected_message is not None:
            self.mode = "edit"
            self.input_buffer = self.selected_message.content
            self.cursor_x = len(self.input_buffer)
        elif key == ord("x") and self.selected_message is not None:
            self.selected_message.expanded = not self.selected_message.expanded
        elif key == ord("d") and self.selected_message is not None:
            self._delete_selected_message()
        elif (
            key in (curses.KEY_UP, curses.KEY_DOWN)
            and self.messages
            and self.selected_message is not None
        ):
            self._move_selection(key)

    def _handle_edit_mode(self, key: int) -> None:
        if key == 27:  # ESC
            self.mode = "select"
            self.input_buffer = ""
            self.cursor_x = 0
        elif key == 10 and self.selected_message is not None:  # Enter
            self.selected_message.content = self.input_buffer
            self.mode = "select"
            self.input_buffer = ""
            self.cursor_x = 0
        else:
            self._handle_text_input(key)

    def _handle_role_mode(self, key: int) -> None:
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
        assert self.selected_message is not None
        self.messages.remove(self.selected_message)
        if self.messages:
            self.selected_message = self.messages[0]
        else:
            self.selected_message = None
            self.mode = "normal"

    def _move_selection(self, key: int) -> None:
        assert self.selected_message is not None
        idx = self.messages.index(self.selected_message)
        if key == curses.KEY_UP:
            self.selected_message = self.messages[max(0, idx - 1)]
        elif key == curses.KEY_DOWN:
            self.selected_message = self.messages[min(len(self.messages) - 1, idx + 1)]


def _role_color(role: str) -> int:
    return (
        curses.COLOR_GREEN
        if role == "user"
        else curses.COLOR_BLUE
        if role == "assistant"
        else curses.COLOR_RED
    )


def _main(stdscr, use_color: bool):
    app = MessageApp(stdscr, use_color)
    app.add_message("Welcome to the Message App!")
    app.add_message(
        "Press 'i' to enter input mode, 's' to enter select mode, 'r' to change role, and 'q' to quit."
    )
    app.add_message(
        "In select mode, use arrow keys to navigate, 'e' to edit, 'x' to expand/collapse, and 'd' to delete."
    )
    app.run()


def main():
    if os.name == "nt":
        print("ncurses UI is not supported on Windows.")
        return
    parser = argparse.ArgumentParser(
        description="Message App with optional color support"
    )
    parser.add_argument("--no-color", action="store_true", help="Disable color output")
    args = parser.parse_args()

    curses.wrapper(lambda stdscr: _main(stdscr, not args.no_color))


if __name__ == "__main__":
    main()
