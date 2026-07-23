"""The gptme Textual application.

Drives conversations with the same machinery as the CLI (``gptme.chat.step``),
but runs generation in a background worker thread so the input stays live:
prompts submitted while the agent is working are queued (#569), and tool
output is rendered in collapsible sections for a compact view.
"""

import contextlib
import contextvars
import io
import logging
import os.path
import re
import sys
import threading
from pathlib import Path
from typing import IO, cast

from rich.markup import escape as markup_escape
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Collapsible, Markdown, Static, TextArea
from textual.worker import Worker, WorkerState

from ..chat import step
from ..constants import DECLINED_CONTENT, INTERRUPT_CONTENT
from ..dirs import get_pt_history_file
from ..hooks import HookType, register_hook, unregister_hook
from ..hooks.cli_confirm import _get_lang_for_tool
from ..hooks.confirm import ConfirmationResult
from ..llm.models import ModelMeta, get_default_model
from ..logmanager import LogManager
from ..message import Message
from ..tools import ToolFormat, ToolUse
from ..tools.base import ToolUse as ToolUseType
from ..tools.complete import SessionCompleteException
from ..util.context import include_paths
from ..util.history import append_history, load_history
from ..util.tokens import len_tokens

logger = logging.getLogger(__name__)

# Max messages rendered when resuming a long conversation
MAX_INITIAL_MESSAGES = 100


def _summarize(content: str, maxlen: int = 80) -> str:
    """One-line summary of message content, for collapsed sections."""
    lines = content.strip().splitlines() or [""]
    first = next((line for line in lines if line.strip()), "").strip()
    # strip codeblock fence from summary
    if first.startswith("```"):
        first = first.lstrip("`") or "output"
    if len(first) > maxlen:
        first = first[: maxlen - 1] + "…"
    n = len(lines)
    return f"{first} ({n} line{'s' if n != 1 else ''})"


_THINK_RE = re.compile(r"<think(?:ing)?>(.*?)</think(?:ing)?>", re.DOTALL)
_THINK_SIG_RE = re.compile(r"<!--\s*think-sig:.*?-->\s*", re.DOTALL)


def _split_thinking(content: str) -> list[tuple[bool, str]]:
    """Split message content into (is_thinking, text) segments.

    Detects <think>/<thinking> blocks and separates them from normal content
    so the TUI can render them in collapsible sections.
    """
    segments: list[tuple[bool, str]] = []
    last_end = 0
    for m in _THINK_RE.finditer(content):
        before = content[last_end : m.start()]
        if before.strip():
            segments.append((False, before))
        inner = _THINK_SIG_RE.sub("", m.group(1)).strip()
        if inner:
            segments.append((True, inner))
        last_end = m.end()
    tail = content[last_end:]
    if tail.strip():
        segments.append((False, tail))
    return segments


class UserMessage(Vertical):
    """A user message, rendered with a distinct border."""

    def __init__(self, content: str, queued: bool = False):
        super().__init__(classes="message user" + (" queued" if queued else ""))
        self.content = content.strip()

    def compose(self) -> ComposeResult:
        label = "User (queued)" if "queued" in self.classes else "User"
        yield Static(Text(label), classes="role")
        yield Markdown(self.content)


class AssistantMessage(Vertical):
    """A completed assistant message, rendered as markdown."""

    def __init__(self, content: str):
        super().__init__(classes="message assistant")
        self.content = content.strip()

    def compose(self) -> ComposeResult:
        yield Static(Text("Assistant"), classes="role")
        segments = _split_thinking(self.content)
        if not any(is_think for is_think, _ in segments):
            yield Markdown(self.content)
        else:
            for is_think, text in segments:
                if is_think:
                    yield Collapsible(
                        Markdown(text),
                        title="Thinking",
                        collapsed=True,
                        classes="thinking-block",
                    )
                elif text.strip():
                    yield Markdown(text)


class SystemMessage(Vertical):
    """A system/tool-output message, collapsed by default (like <details>)."""

    def __init__(self, content: str):
        super().__init__(classes="message system")
        self.content = content.strip()

    def compose(self) -> ComposeResult:
        yield Collapsible(
            Markdown(self.content),
            title=_summarize(self.content),
            collapsed=True,
        )


class StreamingMessage(Vertical):
    """Live view of the assistant response while tokens stream in."""

    def __init__(self) -> None:
        super().__init__(classes="message assistant streaming")
        self._buffer = ""
        self._body = Static(Text("Generating…"), classes="progress-placeholder")

    def compose(self) -> ComposeResult:
        yield Static(Text("Assistant"), classes="role")
        yield self._body

    def append_token(self, token: str) -> None:
        self._buffer += token
        self._body.update(Text(self._buffer))

    def set_thinking(self, is_thinking: bool) -> None:
        """Update the placeholder when the model transitions in/out of thinking."""
        if not self._buffer:
            label = "Thinking…" if is_thinking else "Generating…"
            self._body.update(Text(label))


class ToolPlaceholder(Vertical):
    """Transient widget shown in the chat while a tool is running."""

    def __init__(self) -> None:
        super().__init__(classes="message system tool-placeholder")
        self._body = Static(Text("Running tool…"), classes="progress-placeholder")

    def compose(self) -> ComposeResult:
        yield Static(Text("Tool"), classes="role")
        yield self._body

    def set_tool(self, tool_name: str) -> None:
        self._body.update(Text(f"Running {tool_name}…"))


class InfoMessage(Static):
    """Dim informational line (help text, errors, hints)."""

    def __init__(self, content: str, error: bool = False):
        super().__init__(Text(content), classes="info" + (" error" if error else ""))


class BouncingError(Static):
    """An opt-in error line that briefly draws attention to recovery hints."""

    DEFAULT_CSS = """
    BouncingError {
        border-left: tall $error;
        color: $error;
        padding: 0 1;
    }
    """

    def __init__(self, content: str):
        super().__init__(Text(content), classes="info error")
        self.error_text = content

    def on_mount(self) -> None:
        self.styles.animate("background", "#ff6600", duration=0.1)
        self.set_timer(
            0.1,
            lambda: self.styles.animate("background", "transparent", duration=0.2),
        )
        self.styles.animate("opacity", 0.7, duration=0.06)
        self.set_timer(
            0.06,
            lambda: self.styles.animate("opacity", 1.0, duration=0.18),
        )
        self.set_timer(0.3, self._show_recovery)

    def _show_recovery(self) -> None:
        self.update(
            Text.from_markup(
                f"{markup_escape(self.error_text)}\\n[dim]Recovery:[/dim] edit the command and resubmit, "
                "or retry the action."
            )
        )


def renderables_for_message(msg: Message, expanded: bool = False) -> list:
    """Rich renderables for a message, for native-scrollback (inline) mode."""
    from rich.markdown import Markdown as RichMarkdown
    from rich.padding import Padding

    content = msg.content.strip()
    if msg.role == "user":
        return [
            Text("User", style="bold green"),
            Padding(RichMarkdown(content), (0, 0, 0, 2)),
            Text(),
        ]
    if msg.role == "assistant":
        return [
            Text("Assistant", style="bold blue"),
            Padding(RichMarkdown(content), (0, 0, 0, 2)),
            Text(),
        ]
    # system/tool output: compact summary line, optionally expanded
    renderables: list = [Text(f"▶ {_summarize(content)}", style="dim")]
    if expanded:
        renderables.append(Padding(RichMarkdown(content), (0, 0, 0, 2)))
    renderables.append(Text())
    return renderables


def complete_input(text: str) -> list[str]:
    """Full-line completion candidates, reusing the CLI command completers."""
    from ..commands import get_command_completer, get_user_commands

    if not text.startswith("/"):
        return []
    parts = text.split(None, 1)
    if len(parts) == 1 and not text.endswith(" "):
        # completing the command name itself
        return sorted(c for c in get_user_commands() if c.startswith(text))
    # completing command arguments
    completer = get_command_completer(parts[0][1:])
    if completer is None:
        return []
    arg_text = parts[1] if len(parts) > 1 else ""
    args = arg_text.split()
    partial = args[-1] if args and not arg_text.endswith(" ") else ""
    prev_args = args[:-1] if args and not arg_text.endswith(" ") else args
    base = text[: len(text) - len(partial)]
    try:
        return sorted(
            base + candidate
            for candidate, _desc in completer(partial, prev_args)
            if candidate.startswith(partial)
        )
    except Exception as e:
        logger.debug(f"Command completer error: {e}")
        return []


def _load_pt_history(path: Path) -> list[str]:
    """Read a prompt-toolkit history file; return entries oldest-first."""
    return load_history(path)


def _append_pt_history(path: Path, text: str) -> None:
    """Append one entry under the lock shared with the CLI."""
    append_history(path, text)


class ChatInput(TextArea):
    """Multi-line input: Enter submits, Alt+Enter/Ctrl+J inserts a newline,
    Tab completes slash-commands, Up/Down navigates history."""

    class Submitted(TextualMessage):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class CompletionsChanged(TextualMessage):
        """Posted when the tab-completion candidate list changes.
        candidates=[] means hide the overlay."""

        def __init__(self, candidates: list[str], selected: int) -> None:
            super().__init__()
            self.candidates = candidates
            self.selected = selected

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tab_candidates: list[str] = []
        self._tab_index = -1
        self._tab_last = ""
        self._history_file = get_pt_history_file()
        self._history: list[str] = _load_pt_history(self._history_file)
        self._history_idx = -1  # -1 = not browsing; 0 = most recent
        self._history_saved = ""  # text buffered when browsing started
        self._history_edits: dict[int, str] = {}

    def _push_history(self, text: str) -> None:
        """Record a submitted entry and persist it to the shared history file."""
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
            try:
                _append_pt_history(self._history_file, text)
            except OSError as e:
                logger.warning(
                    "failed to persist history to %s: %s", self._history_file, e
                )
        self._history_idx = -1
        self._history_saved = ""
        self._history_edits.clear()

    def _clear_completions(self) -> None:
        """Clear tab completion state and hide the overlay."""
        if self._tab_candidates:
            self._tab_candidates = []
            self._tab_index = -1
            self.post_message(self.CompletionsChanged([], -1))

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self._clear_completions()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in ("alt+enter", "shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self._clear_completions()
            self.insert("\n")
            return
        if event.key == "tab":
            # consume Tab so it completes instead of switching focus
            event.stop()
            event.prevent_default()
            self._cycle_completion()
            return
        # Any non-Tab key dismisses the completion overlay
        if self._tab_candidates:
            self._clear_completions()
        if event.key == "up":
            row, _ = self.cursor_location
            if row == 0 and self._history:
                event.stop()
                event.prevent_default()
                if self._history_idx == -1:
                    self._history_saved = self.text
                else:
                    self._history_edits[self._history_idx] = self.text
                new_idx = self._history_idx + 1
                if new_idx < len(self._history):
                    self._history_idx = new_idx
                    self._set_text(
                        self._history_edits.get(new_idx, self._history[-(new_idx + 1)])
                    )
                return
        if event.key == "down":
            lines = self.text.split("\n")
            row, _ = self.cursor_location
            if row == len(lines) - 1 and self._history_idx >= 0:
                event.stop()
                event.prevent_default()
                self._history_edits[self._history_idx] = self.text
                new_idx = self._history_idx - 1
                if new_idx < 0:
                    self._history_idx = -1
                    self._set_text(self._history_saved)
                else:
                    self._history_idx = new_idx
                    self._set_text(
                        self._history_edits.get(new_idx, self._history[-(new_idx + 1)])
                    )
                return
        if event.key == "alt+left":
            event.stop()
            event.prevent_default()
            self.action_cursor_word_left()
            return
        if event.key == "alt+right":
            event.stop()
            event.prevent_default()
            self.action_cursor_word_right()
            return
        await super()._on_key(event)

    def _set_text(self, text: str) -> None:
        self.text = text
        self.move_cursor(self.document.end)

    def _cycle_completion(self) -> None:
        if "\n" in self.text:
            return  # commands are single-line
        if self._tab_candidates and self.text == self._tab_last:
            # repeated Tab: cycle through candidates
            self._tab_index = (self._tab_index + 1) % len(self._tab_candidates)
            self._set_text(self._tab_candidates[self._tab_index])
        else:
            candidates = complete_input(self.text)
            if not candidates:
                return
            self._tab_candidates = candidates
            self._tab_index = -1
            prefix = os.path.commonprefix(candidates)
            if len(candidates) == 1:
                self._set_text(candidates[0])
                self._tab_candidates = []
            elif prefix and prefix != self.text:
                self._set_text(prefix)
            else:
                self._tab_index = 0
                self._set_text(candidates[0])
        self._tab_last = self.text
        # Notify the app to show/update the completion overlay
        if len(self._tab_candidates) > 1:
            self.post_message(
                self.CompletionsChanged(self._tab_candidates, self._tab_index)
            )
        else:
            self.post_message(self.CompletionsChanged([], -1))


class ConfirmScreen(ModalScreen[ConfirmationResult]):
    """Modal asking the user to confirm a tool execution."""

    BINDINGS = [
        Binding("y,enter", "confirm", "Execute"),
        Binding("n,escape", "skip", "Skip"),
        Binding("a", "auto", "Auto-confirm session"),
    ]

    def __init__(self, tool_use: ToolUseType | None, preview: str | None):
        super().__init__()
        self.tool_name = tool_use.tool if tool_use else "tool"
        content = preview or (tool_use.content if tool_use else "") or ""
        self.preview_content = content
        self.lang = _get_lang_for_tool(self.tool_name, content)

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(
                Text(f"Execute {self.tool_name}?", style="bold"), id="confirm-title"
            )
            with VerticalScroll(id="confirm-preview"):
                yield Static(
                    Syntax(
                        self.preview_content,
                        self.lang,
                        word_wrap=True,
                        background_color="default",
                    )
                )
            yield Static(
                Text("[y] execute   [n] skip   [a] auto-confirm session"),
                id="confirm-help",
            )

    def action_confirm(self) -> None:
        self.dismiss(ConfirmationResult.confirm())

    def action_skip(self) -> None:
        self.dismiss(ConfirmationResult.skip("Declined by user"))

    def action_auto(self) -> None:
        cast("GptmeApp", self.app).auto_confirm = True
        self.dismiss(ConfirmationResult.confirm())


class GptmeApp(App):
    """gptme TUI: streaming chat with live input, queueing and collapsible output."""

    TITLE = "gptme"

    CSS = """
    Screen:inline {
        height: auto;
        max-height: 40%;
    }
    #live {
        height: auto;
        max-height: 8;
        margin: 0 1;
    }
    #chat {
        padding: 0 1;
    }
    .message {
        height: auto;
        margin: 1 0 0 0;
        background: transparent;
    }
    .message > .role {
        color: $text-muted;
        text-style: bold;
    }
    .message.user {
        border-left: thick $success;
        padding-left: 1;
    }
    .message.user > .role {
        color: $success;
    }
    .message.user.queued {
        opacity: 0.5;
    }
    .message.assistant {
        border-left: thick $primary;
        padding-left: 1;
    }
    .message.assistant > .role {
        color: $primary;
    }
    .message.system {
        border-left: thick $surface-lighten-2;
        padding-left: 1;
    }
    .message.system Collapsible {
        border: none;
        padding: 0;
        background: transparent;
    }
    .thinking-block {
        border: none;
        padding: 0;
        background: transparent;
        color: $text-muted;
    }
    .thinking-block CollapsibleTitle {
        color: $text-muted;
        text-style: italic;
    }
    .thinking-block Markdown {
        color: $text-muted;
    }
    /* compact markdown: the widget defaults add blank lines around
       codeblocks (MarkdownFence margin 1 0) and after the last block */
    .message Markdown {
        padding: 0;
        background: transparent;
    }
    .message Static {
        background: transparent;
    }
    .message > .progress-placeholder {
        color: $text-muted;
        text-style: italic;
    }
    .message MarkdownFence {
        margin: 0;
    }
    .message MarkdownBlock:last-child {
        margin-bottom: 0;
    }
    .info {
        color: $text-muted;
        margin: 1 0 0 1;
    }
    .info.error {
        color: $error;
    }
    #bottom {
        dock: bottom;
        height: auto;
    }
    #input {
        margin: 1 1 0 1;
        height: auto;
        max-height: 10;
    }
    #input-hint {
        height: 1;
        margin: 0 1;
        color: $text-muted;
    }
    #status {
        height: 1;
        margin-top: 1;
        padding: 0 1;
        color: $text-muted;
    }
    Screen:inline #status {
        margin-top: 0;
    }
    ConfirmScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: 80%;
        max-height: 80%;
        height: auto;
        border: round $warning;
        background: $surface;
        padding: 1;
    }
    #confirm-preview {
        height: auto;
        max-height: 20;
        margin: 1 0;
    }
    #confirm-help {
        color: $text-muted;
    }
    #completions {
        display: none;
        height: auto;
        max-height: 10;
        margin: 0 1;
        padding: 0 1;
        background: $surface;
        border: round $primary;
    }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "Interrupt", show=True),
        Binding("ctrl+c", "interrupt_or_quit", "Interrupt/Quit", priority=True),
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+o", "toggle_outputs", "Expand/collapse outputs", show=True),
    ]

    def __init__(
        self,
        manager: LogManager,
        tool_format: ToolFormat = "markdown",
        workspace: Path | None = None,
        auto_confirm: bool = False,
        inline: bool = False,
        experimental_jelly_errors: bool = False,
    ):
        super().__init__()
        self.manager = manager
        self.tool_format: ToolFormat = tool_format
        self.workspace = workspace or manager.workspace
        self.auto_confirm = auto_confirm
        self.inline = inline
        self.experimental_jelly_errors = experimental_jelly_errors
        self._stream_text = ""
        # Snapshot the caller's context (default model, output format, …) so
        # worker threads see it — gptme stores this state in ContextVars,
        # which fresh threads do not inherit. Workers run sequentially
        # (exclusive=True), so reusing one Context is safe, and mutations
        # (e.g. /model-style changes) persist across turns.
        self._chat_ctx = contextvars.copy_context()
        self.prompt_queue: list[str] = []
        self._queued_widgets: list[Widget] = []
        self.generating = False
        self.state = "idle"
        self._interrupt_event = threading.Event()
        self._quitting = False
        self._stream_widget: StreamingMessage | None = None
        self._tool_placeholder: ToolPlaceholder | None = None
        self._outputs_expanded = False
        self._stdio_sink: IO[str] | None = None
        self._real_stdout: IO[str] | None = None
        self._real_stderr: IO[str] | None = None
        self._term_attrs: list | None = None
        # tracked separately for the status bar: the authoritative value
        # lives in a ContextVar inside _chat_ctx, which the UI thread
        # cannot enter while a worker is running
        self._model: ModelMeta | None = None

    # ------------------------------------------------------------------ UI

    def compose(self) -> ComposeResult:
        if self.inline:
            # native-scrollback mode: no in-app chat view; completed messages
            # are printed into the terminal's scrollback via _print_above
            yield Static(id="live")
            yield ChatInput(id="input")
            yield Static(
                Text("Type a message… (Enter to send, Alt+Enter for newline)"),
                id="input-hint",
            )
            yield Static(id="status")
            return
        yield VerticalScroll(id="chat")
        with Vertical(id="bottom"):
            yield Static("", id="completions")
            yield ChatInput(id="input")
            yield Static(
                Text("Type a message… (Enter to send, Alt+Enter for newline)"),
                id="input-hint",
            )
            yield Static(id="status")

    def on_mount(self) -> None:
        # Redirect stdout/stderr to a log file: core machinery (tool output
        # streaming, terminal titles, stray logs) writes to stdout, which
        # would corrupt the display. The Textual driver renders via
        # sys.__stderr__ and is unaffected.
        self._stdio_sink = open(self.manager.logdir / "tui.log", "a", buffering=1)
        self._real_stdout, self._real_stderr = sys.stdout, sys.stderr
        sys.stdout = self._stdio_sink
        sys.stderr = self._stdio_sink

        # Snapshot raw-mode terminal attributes as set up by the Textual
        # driver, so we can restore them after tools (e.g. ipython) reset
        # the terminal to cooked/echo mode — which would otherwise echo
        # mouse-tracking reports as garbage input.
        try:
            import termios

            self._term_attrs = termios.tcgetattr(sys.__stdin__.fileno())  # type: ignore[union-attr]
        except Exception:
            self._term_attrs = None

        self._model = get_default_model()
        self.sub_title = self.manager.logdir.name
        # confirmation dialog for tool execution; takes precedence over the
        # CLI hook (priority 0) registered by init()
        register_hook(
            name="tui_confirm",
            hook_type=HookType.TOOL_CONFIRM,
            func=self._tui_confirm_hook,
            priority=100,
        )
        if not self.inline:
            # inline mode prints history to the terminal before the app starts
            self._render_history()
            # prevent the chat scroll area from stealing focus; input is always
            # the active widget for keyboard input
            self.query_one("#chat", VerticalScroll).can_focus = False
        self.query_one("#input", ChatInput).focus()
        self._update_status()
        # watchdog: tools can reset the tty at any point during execution
        self.set_interval(0.5, self._restore_terminal)

    def on_unmount(self) -> None:
        unregister_hook("tui_confirm", HookType.TOOL_CONFIRM)
        if self._real_stdout is not None:
            sys.stdout = self._real_stdout
        if self._real_stderr is not None:
            sys.stderr = self._real_stderr
        if self._stdio_sink is not None:
            self._stdio_sink.close()
            self._stdio_sink = None

    def _render_history(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        msgs = [m for m in self.manager.log if not m.hide]
        if len(msgs) > MAX_INITIAL_MESSAGES:
            chat.mount(
                InfoMessage(
                    f"… {len(msgs) - MAX_INITIAL_MESSAGES} earlier messages not shown"
                )
            )
            msgs = msgs[-MAX_INITIAL_MESSAGES:]
        for msg in msgs:
            widget = self._widget_for(msg)
            if widget:
                chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)

    def _widget_for(self, msg: Message) -> Widget | None:
        if msg.role == "user":
            return UserMessage(msg.content)
        if msg.role == "assistant":
            return AssistantMessage(msg.content)
        if msg.role == "system":
            return SystemMessage(msg.content)
        return None

    def _print_above(self, *renderables) -> None:
        """Print rich renderables into the terminal's native scrollback,
        above the inline app (must be called on the UI thread).

        Exploits the inline driver's frame invariant: between frames the
        cursor sits at the top-left of the app region. Clear the region,
        write the content (scrolling the terminal naturally), and repaint
        the app below it.
        """
        driver = self._driver
        if driver is None:
            return
        from rich.console import Console as RichConsole
        from rich.control import Control

        buf = io.StringIO()
        console = RichConsole(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=self.size.width or 80,
        )
        for renderable in renderables:
            console.print(renderable)
        # Between frames the cursor is parked at the caret position, offset
        # from the region origin by App._previous_cursor_position (see
        # App._display's inline branch). Move back to the origin, clear the
        # region, print (scrolling the terminal), and park the cursor at the
        # caret offset again so the next frame's relative move stays correct.
        caret = getattr(self, "_previous_cursor_position", None)
        sequence = ""
        if caret is not None:
            sequence += Control.move(-caret.x, -caret.y).segment.text
        sequence += "\x1b[J" + buf.getvalue()
        if caret is not None:
            sequence += Control.move(caret.x, caret.y).segment.text
        driver.write(sequence)
        self.refresh()

    def _show_message(self, msg: Message) -> None:
        if self.inline:
            self._print_above(*renderables_for_message(msg, self._outputs_expanded))
            return
        widget = self._widget_for(msg)
        if widget:
            self._mount_in_chat(widget)

    def _show_info(self, text: str, error: bool = False) -> None:
        if self.inline:
            self._print_above(Text(text, style="red" if error else "bright_black"))
            return
        widget: Widget
        if error and self.experimental_jelly_errors:
            widget = BouncingError(text)
        else:
            widget = InfoMessage(text, error=error)
        self._mount_in_chat(widget)

    def _mount_in_chat(self, widget: Widget) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        stick = chat.scroll_offset.y >= chat.max_scroll_y - 2
        chat.mount(widget)
        if stick:
            self.call_after_refresh(chat.scroll_end, animate=False)

    def _restore_terminal(self) -> None:
        """Reassert raw mode if tool execution reset the tty (e.g. ipython).

        Cooked mode line-buffers input (keys appear dead) and echoes typed
        chars and mouse reports as garbage. Only writes when attrs drifted,
        so it is cheap enough to call often (also runs on an interval while
        the app is alive, since tools can reset the tty mid-execution).
        """
        if self._term_attrs is None:
            return
        with contextlib.suppress(Exception):
            import termios

            fd = sys.__stdin__.fileno()  # type: ignore[union-attr]
            if termios.tcgetattr(fd) != self._term_attrs:
                termios.tcsetattr(fd, termios.TCSANOW, self._term_attrs)

    def _update_status(self) -> None:
        parts = []
        model = self._model
        if model:
            parts.append(model.full)
            try:
                tokens = len_tokens(self.manager.log.messages, model.model)
                pct = 100 * tokens / model.context if model.context else 0
                parts.append(f"{tokens // 1000}k/{model.context // 1000}k ({pct:.0f}%)")
            except Exception:  # token counting must never break the UI
                pass
        parts.append(self.state)
        if self.prompt_queue:
            parts.append(f"{len(self.prompt_queue)} queued")
        self.query_one("#status", Static).update(Text(" | ".join(parts)))

    def _set_state(self, state: str) -> None:
        self.state = state
        self._update_status()

    # -------------------------------------------------------------- input

    def on_chat_input_completions_changed(
        self, event: ChatInput.CompletionsChanged
    ) -> None:
        """Show or hide the tab-completion overlay above the input."""
        try:
            w = self.query_one("#completions", Static)
        except Exception:
            return
        candidates = event.candidates
        if len(candidates) <= 1:
            w.display = False
            return
        max_visible = 8
        start = max(
            0,
            min(
                event.selected - max_visible // 2,
                len(candidates) - max_visible,
            ),
        )
        visible = candidates[start : start + max_visible]
        t = Text()
        for i, c in enumerate(visible, start=start):
            prefix = "▶ " if i == event.selected else "  "
            style = "bold" if i == event.selected else "dim"
            t.append(f"{prefix}{c}\n", style=style)
        w.update(t)
        w.display = True

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.value.strip()
        chat_input = self.query_one("#input", ChatInput)
        chat_input.text = ""
        if text:
            chat_input._push_history(text)
        logger.debug(
            "input submitted: %r (generating=%s, queue=%d)",
            text[:80],
            self.generating,
            len(self.prompt_queue),
        )
        if not text:
            # empty submit resumes generation (e.g. after interrupt/decline)
            if not self.generating and self._can_resume():
                self._start_generation()
            return
        from ..util.content import is_message_command

        if is_message_command(text):
            # a real /command; paths like /tmp/foo.md fall through to _submit,
            # which attaches file contents via include_paths
            self._handle_command(text)
            return
        if self.generating:
            self.prompt_queue.append(text)
            if self.inline:
                self._print_above(Text(f"(queued) {text}", style="bright_black"))
            else:
                widget = UserMessage(text, queued=True)
                self._queued_widgets.append(widget)
                self._mount_in_chat(widget)
            self._update_status()
        else:
            self._submit(text)

    def _can_resume(self) -> bool:
        last = next((m for m in reversed(self.manager.log) if not m.hide), None)
        return last is not None and last.role != "assistant"

    # commands that take over the terminal (e.g. spawn $EDITOR), which would
    # corrupt the TUI display
    UNSUPPORTED_COMMANDS = frozenset({"edit"})

    def _handle_command(self, text: str) -> None:
        """Run a slash-command through the CLI command registry."""
        from ..commands import execute_cmd

        cmd = text.split()[0].lstrip("/")
        if cmd in ("quit", "q"):  # TUI-local alias for /exit
            self.exit()
            return
        if cmd in self.UNSUPPORTED_COMMANDS:
            self._show_info(
                f"/{cmd} takes over the terminal and is not supported in the "
                "TUI; resume this conversation in the CLI to use it."
            )
            return
        if self.generating:
            self._show_info(
                "Commands can't run while the agent is working; retry when idle."
            )
            return

        msg = Message("user", text, quiet=True)
        before = self.manager.log.messages
        self.manager.append(msg)
        # capture command output (print/rich console both write to sys.stdout);
        # give commands an empty stdin so ones that prompt interactively fail
        # fast (EOFError) instead of hanging the UI on a raw-mode tty
        buf = io.StringIO()
        real_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("")
            with contextlib.redirect_stdout(buf):
                # run inside the chat context so state changes (e.g. /model
                # switching the default-model ContextVar) are seen by workers
                handled = self._chat_ctx.run(execute_cmd, msg, self.manager)
        except SystemExit:  # /exit
            self.exit()
            return
        except EOFError:
            self._show_info(
                f"/{cmd} needs interactive input, which the TUI doesn't "
                f"support; pass arguments directly (e.g. /{cmd} <args>) or "
                "use the CLI.",
                error=True,
            )
            return
        except Exception as e:
            logger.exception("Command failed")
            self._show_info(f"Command failed: {e}", error=True)
            return
        finally:
            sys.stdin = real_stdin

        self._model = self._chat_ctx.run(get_default_model)
        if self.manager.log.messages != before:
            # command changed the log (undo, appended messages, …): re-render
            self._rebuild_chat()
        if output := buf.getvalue().strip():
            self._show_info(output)
        if not handled:
            logger.warning("Command %r not handled by registry", text)
            self._show_info(f"Unknown command: /{cmd}")
        self._update_status()

    def _rebuild_chat(self) -> None:
        if self.inline:
            # scrollback can't be rewritten; the change is noted via output
            return
        chat = self.query_one("#chat", VerticalScroll)
        chat.remove_children()
        self._render_history()

    def _submit(self, text: str) -> None:
        msg = Message("user", text, quiet=True)
        msg = include_paths(msg, self.workspace)
        self.manager.append(msg)
        self._show_message(msg)
        self._start_generation()

    # --------------------------------------------------------- generation

    def _start_generation(self) -> None:
        logger.debug("starting generation worker")
        self.generating = True
        self._interrupt_event.clear()
        self._set_state("generating")
        self.run_worker(
            self._generation_worker, thread=True, exclusive=True, group="generation"
        )

    def _generation_worker(self) -> None:
        """Thread worker: run the step loop inside the captured chat context."""
        self._chat_ctx.run(self._generation_body)

    def _generation_body(self) -> None:
        """Runs the step loop until no more runnable tools."""
        manager = self.manager
        max_steps: int | None = None
        if max_steps_str := os.environ.get("GPTME_MAX_STEPS"):
            with contextlib.suppress(ValueError):
                max_steps = int(max_steps_str)
        step_count = 0
        try:
            while True:
                self.call_from_thread(self._begin_stream)
                interrupted = False
                declined = False
                try:
                    for msg in step(
                        manager.log,
                        stream=True,
                        tool_format=self.tool_format,
                        workspace=self.workspace,
                        logdir=manager.logdir,
                        on_token=self._on_token,
                        on_thinking=self._on_thinking,
                    ):
                        # each yield may follow a tool execution that reset
                        # the tty (e.g. ipython init) — reassert raw mode
                        # immediately, not just at end of step, so later
                        # confirmations in the same step get a sane terminal
                        self._restore_terminal()
                        manager.append(msg)
                        if msg.content == DECLINED_CONTENT:
                            declined = True
                        self.call_from_thread(self._on_step_message, msg)
                except KeyboardInterrupt:
                    interrupted = True
                    msg = Message("system", INTERRUPT_CONTENT)
                    manager.append(msg)
                    self.call_from_thread(self._on_step_message, msg)
                finally:
                    # tools (e.g. ipython) may have reset the tty out of raw mode
                    self._restore_terminal()

                if interrupted or declined or self._interrupt_event.is_set():
                    break
                step_count += 1
                if max_steps is not None and step_count >= max_steps:
                    self.call_from_thread(
                        self._show_info,
                        f"Reached max steps limit ({max_steps}), stopping.",
                    )
                    break
                # continue stepping while the last assistant msg has runnable tools
                last_content = next(
                    (m.content for m in reversed(manager.log) if m.role == "assistant"),
                    "",
                )
                if not any(
                    t.is_runnable for t in ToolUse.iter_from_content(last_content)
                ):
                    break
        except SessionCompleteException:
            # complete tool is filtered out in interactive TUI mode, but a
            # resumed autonomous conversation may still have it loaded
            self.call_from_thread(self._show_info, "Session marked complete.")
        except Exception as e:
            logger.exception("Error in generation worker")
            self.call_from_thread(self._show_info, f"Error: {e}", True)
        # NOTE: completion handling (queue dispatch etc.) happens in
        # on_worker_state_changed, which fires only after this thread has
        # fully exited self._chat_ctx — dispatching from here would make the
        # next worker re-enter a still-entered Context and crash.

    def _on_token(self, token: str) -> None:
        # called from the worker thread, per streamed line/chunk
        if self._interrupt_event.is_set():
            raise KeyboardInterrupt
        self.call_from_thread(self._on_stream_token, token)

    def _on_thinking(self, is_thinking: bool) -> None:
        # called from the worker thread when thinking state changes
        self.call_from_thread(self._set_stream_thinking, is_thinking)

    def _set_stream_thinking(self, is_thinking: bool) -> None:
        if self._stream_widget is not None:
            self._stream_widget.set_thinking(is_thinking)
        if self.inline:
            label = "Thinking…" if is_thinking else "Generating…"
            with contextlib.suppress(Exception):
                self.query_one("#live", Static).update(Text(label, style="italic"))

    def _begin_stream(self) -> None:
        # A new model step starts only after the previous tool batch finished.
        self._clear_tool_placeholder()
        self._set_state("generating")
        if self.inline:
            self.query_one("#live", Static).update(Text("Generating…", style="italic"))
        elif self._stream_widget is None:
            self._stream_widget = StreamingMessage()
            self._mount_in_chat(self._stream_widget)

    def _on_stream_token(self, token: str) -> None:
        if self.inline:
            # live preview in the inline region: show the last few lines
            self._stream_text += token
            lines = self._stream_text.strip().splitlines()[-6:]
            self.query_one("#live", Static).update(
                Text("\n".join(lines), style="bright_black")
            )
            return
        if self._stream_widget is None:
            self._stream_widget = StreamingMessage()
            self._mount_in_chat(self._stream_widget)
        chat = self.query_one("#chat", VerticalScroll)
        stick = chat.scroll_offset.y >= chat.max_scroll_y - 2
        self._stream_widget.append_token(token)
        if stick:
            self.call_after_refresh(chat.scroll_end, animate=False)

    def _clear_stream_view(self) -> None:
        self._stream_text = ""
        if self.inline:
            self.query_one("#live", Static).update("")
        if self._stream_widget is not None:
            self._stream_widget.remove()
            self._stream_widget = None

    def _show_tool_placeholder(self) -> None:
        if self.inline:
            self.query_one("#live", Static).update(
                Text("Running tool…", style="italic")
            )
            return
        if self._tool_placeholder is not None:
            return
        self._tool_placeholder = ToolPlaceholder()
        self._mount_in_chat(self._tool_placeholder)

    def _clear_tool_placeholder(self) -> None:
        if self.inline:
            self.query_one("#live", Static).update("")
            return
        if self._tool_placeholder is not None:
            self._tool_placeholder.remove()
            self._tool_placeholder = None

    def _on_step_message(self, msg: Message) -> None:
        if msg.role == "assistant":
            # replace the live stream view with the final rendering
            self._clear_stream_view()
        if msg.role == "system":
            # Keep the indicator at the bottom while results and hook messages
            # arrive. The next model step or worker completion ends the batch.
            self._clear_tool_placeholder()
        self._show_message(msg)
        if msg.role == "assistant" and any(
            tool_use.is_runnable for tool_use in ToolUse.iter_from_content(msg.content)
        ):
            self._set_state("executing tools")
            self._show_tool_placeholder()
        elif msg.role == "system" and self.state == "executing tools":
            self._show_tool_placeholder()
        self._update_status()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "generation":
            return
        if event.state in (
            WorkerState.SUCCESS,
            WorkerState.ERROR,
            WorkerState.CANCELLED,
        ):
            self._generation_done()

    def _generation_done(self) -> None:
        self.generating = False
        # stream may have ended without a final message (interrupt mid-stream)
        self._clear_stream_view()
        self._clear_tool_placeholder()
        if self._interrupt_event.is_set() and self.prompt_queue:
            # user interrupted: hand queued text back instead of auto-submitting
            text = "\n".join(self.prompt_queue)
            self.prompt_queue.clear()
            for w in self._queued_widgets:
                w.remove()
            self._queued_widgets.clear()
            self.query_one("#input", ChatInput)._set_text(text)
        elif self.prompt_queue:
            text = self.prompt_queue.pop(0)
            if self._queued_widgets:
                self._queued_widgets.pop(0).remove()
            self._set_state("idle")
            self._submit(text)
            return
        self._set_state("idle")

    # ------------------------------------------------------- confirmation

    def _tui_confirm_hook(
        self,
        tool_use: ToolUseType,
        preview: str | None = None,
        workspace: Path | None = None,
    ) -> ConfirmationResult:
        """TOOL_CONFIRM hook; called from the worker thread, blocks on dialog."""
        if self.auto_confirm:
            return ConfirmationResult.confirm()

        # a previously-executed tool in this step may have left the tty in
        # cooked/echo mode (ICANON line-buffers input, so dialog keys would
        # not respond and typed chars/mouse reports would echo as garbage)
        self._restore_terminal()

        result: dict[str, ConfirmationResult] = {}
        done = threading.Event()

        def show() -> None:
            self._set_state("awaiting confirmation")

            def on_result(r: ConfirmationResult | None) -> None:
                result["r"] = r or ConfirmationResult.skip("Dialog dismissed")
                done.set()

            self.push_screen(ConfirmScreen(tool_use, preview), on_result)

        self.call_from_thread(show)
        while not done.wait(timeout=0.5):
            if self._quitting:
                return ConfirmationResult.skip("App exiting")
        self._restore_terminal()
        self.call_from_thread(self._set_state, "executing tools")
        return result["r"]

    # ------------------------------------------------------------ actions

    def action_interrupt(self) -> None:
        if self.generating:
            self._interrupt_event.set()
            self._set_state("interrupting…")

    def action_interrupt_or_quit(self) -> None:
        if self.generating:
            self.action_interrupt()
        else:
            self.exit()

    def action_toggle_outputs(self) -> None:
        self._outputs_expanded = not self._outputs_expanded
        if self.inline:
            # scrollback is immutable; the toggle affects future tool output
            self._show_info(
                "Tool output will be printed "
                + ("expanded" if self._outputs_expanded else "collapsed")
                + " from now on."
            )
            return
        for collapsible in self.query(Collapsible):
            collapsible.collapsed = not self._outputs_expanded

    async def action_quit(self) -> None:
        self._quitting = True
        self.exit()
