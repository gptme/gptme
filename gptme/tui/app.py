"""The gptme Textual application.

Drives conversations with the same machinery as the CLI (``gptme.chat.step``),
but runs generation in a background worker thread so the input stays live:
prompts submitted while the agent is working are queued (#569), and tool
output is rendered in collapsible sections for a compact view.
"""

import asyncio
import contextlib
import contextvars
import functools
import io
import json
import logging
import math
import os.path
import re
import signal
import sys
from collections.abc import Awaitable, Generator, Sequence
from dataclasses import dataclass

try:
    import termios
except ImportError:
    termios = None  # type: ignore[assignment]
import threading
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast
from uuid import uuid4

from rich.console import Console as RichConsole
from rich.control import Control
from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape as markup_escape
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual._arrange import DockArrangeResult
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.filter import ANSIToTruecolor
from textual.geometry import Offset, Size
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Collapsible,
    Markdown,
    Static,
    TextArea,
)
from textual.widgets.text_area import Edit, EditResult, Selection
from textual.worker import Worker, WorkerState

from ..chat import step
from ..commands import execute_cmd, get_command_completer, get_user_commands
from ..config import get_config
from ..constants import (
    DECLINED_CONTENT,
    INTERRUPT_CONTENT,
    color_to_hex,
    configured_role_color,
)
from ..dirs import get_pt_history_file
from ..hooks import HookType, register_hook, trigger_hook, unregister_hook
from ..hooks.cli_confirm import _get_lang_for_tool
from ..hooks.confirm import ConfirmationResult
from ..hooks.types import ToolExecutePreData
from ..lessons.skill_events import (
    SkillPhase,
    abandon_skill_invocations,
    record_skill_phase,
    skill_invocation_owner,
)
from ..llm.models import ModelMeta, get_default_model
from ..llm.recovery import RetryNotice, recovery_notifications
from ..logmanager import LogManager
from ..message import Message
from ..prompt_queue import drain_prompt_queue
from ..tools import ToolFormat, ToolUse
from ..tools.base import ToolUse as ToolUseType
from ..tools.base import get_tool_format
from ..tools.complete import SessionCompleteException
from ..tools.restart import (
    Interface,
    RestartError,
    parse_restart_target,
    prepare_web_switch,
)
from ..util.content import is_message_command
from ..util.context import extract_urls, include_paths
from ..util.cost_display import inline_cost_text
from ..util.cost_tracker import CostTracker, session_id_for_logdir
from ..util.history import append_history, load_history
from ..util.interrupt import (
    clear_pending,
    kill_processes,
    raise_in_thread,
    start_tracking,
    stop_tracking,
)
from ..util.tokens import len_tokens
from .statusline import LogStats, build_payload, run_statusline

if TYPE_CHECKING:
    from textual.timer import Timer

logger = logging.getLogger(__name__)

# Max messages rendered when resuming a long conversation
MAX_INITIAL_MESSAGES = 100
# Max lines of the inline queued-prompts view (matches #queued max-height)
MAX_QUEUED_SHOWN = 5


def _queued_prompt_text(item: str | Message) -> str:
    """Return the user-visible text for a TUI-queued prompt."""
    return item.content if isinstance(item, Message) else item


SUMMARY_MAXLEN = 80


def _drop_summary_line(content: str) -> str:
    """Body for an expanded section whose title is ``_summarize(content)``.

    The title already shows the first line, so don't repeat it, unless the
    title doesn't show it in full: a code fence (the title then just says
    "output") or a line truncated to fit.
    """
    lines = content.strip().splitlines()
    if not lines:
        return content
    first = lines[0].strip()
    if first.startswith("```") or len(first) > SUMMARY_MAXLEN:
        return content
    return "\n".join(lines[1:]).strip("\n") or content


def _summarize(content: str, maxlen: int = SUMMARY_MAXLEN) -> str:
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


_THINK_RE = re.compile(
    r"<think(?:ing)?(?:\s[^>]*)?>(.*?)</think(?:ing)?(?:\s[^>]*)?>", re.DOTALL
)
_THINK_OPEN_RE = re.compile(r"<think(?:ing)?(?:\s[^>]*)?>")
_THINK_SIG_RE = re.compile(r"<!--\s*think-sig:.*?-->\s*", re.DOTALL)

# Matches the `tool` format: @tool_name(call_id): {...json...}. Calls end at
# the next tool-call header, any following prose (indented or not), or the end
# of the message. JSON string braces are handled by parsing the captured body.
_TOOL_CALL_RE = re.compile(
    r"^(@(\w+)\([^)]*\)):\s*(\{.*?\})(?=\n@\w+\([^)]*\):|\n|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Matches XML tool-call blocks (<tool-use>...</tool-use> or <function_calls>...</function_calls>).
_XML_TOOLUSE_RE = re.compile(
    r"(<tool-use>.*?</tool-use>|<function_calls>.*?</function_calls>)",
    re.DOTALL | re.IGNORECASE,
)


def _split_thinking(content: str) -> list[tuple[bool, str]]:
    """Split message content into (is_thinking, text) segments.

    Detects <think>/<thinking> blocks and separates them from normal content
    so the TUI can render them in collapsible sections. An unclosed trailing
    block (interrupted or malformed output) is treated as thinking too, matching
    ``_strip_thinking`` — otherwise hidden mode would print the reasoning as
    ordinary content.
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
    if open_m := _THINK_OPEN_RE.search(tail):
        before = tail[: open_m.start()]
        if before.strip():
            segments.append((False, before))
        inner = _THINK_SIG_RE.sub("", tail[open_m.end() :]).strip()
        if inner:
            segments.append((True, inner))
    elif tail.strip():
        segments.append((False, tail))
    return segments


def _strip_thinking(text: str) -> str:
    """Drop thinking from (possibly still-streaming) content, for previews.

    Removes closed ``<think>`` blocks and cuts an unclosed trailing one.
    """
    text = _THINK_RE.sub("", text)
    if m := _THINK_OPEN_RE.search(text):
        text = text[: m.start()]
    return text


def _thinking_stub(text: str) -> str:
    """One-line placeholder for a hidden thinking block."""
    n = len(text.splitlines())
    lines = f"{n} line{'s' if n != 1 else ''}"
    return f"▸ Thinking ({lines} hidden, /display thinking to show)"


def _role_label(role: str) -> str:
    """Display name for a message role, matching the CLI.

    Uses the configured user name and the agent name from ``[agent].name``
    in gptme.toml, falling back to the capitalized role.
    """
    config = get_config()
    if role == "user":
        return config.user.user.name
    if role == "assistant":
        agent_config = config.chat and config.chat.agent_config
        if agent_config and agent_config.name:
            return agent_config.name
    return role.capitalize()


def _show_thinking_default() -> bool:
    """Whether thinking is displayed at startup (``GPTME_TUI_DISPLAY_THINKING``)."""
    return bool(get_config().get_env_bool("GPTME_TUI_DISPLAY_THINKING", default=False))


def _group_turns_default() -> bool:
    """Whether an agent turn's steps and tool results show as one block
    (``GPTME_TUI_DISPLAY_GROUPING``; off: one block per log message)."""
    return bool(get_config().get_env_bool("GPTME_TUI_DISPLAY_GROUPING", default=True))


def _flow_kind(msg: Message) -> str:
    """Where a message sits in a turn: "user", "step" (an assistant step),
    "result" (output of one of its tool calls) or "other" (e.g. hook output)."""
    if msg.role in ("user", "assistant"):
        return "user" if msg.role == "user" else "step"
    if msg.call_id or "tool_call" in (msg.metadata or {}):
        return "result"
    return "other"


def _call_index(msg: Message) -> int | None:
    """Which tool call of its step a result came from (see execute_msg)."""
    call = (msg.metadata or {}).get("tool_call")
    return call if isinstance(call, int) else None


def _keep_turn_place(widget: Widget, replaced: Widget) -> None:
    """A widget replacing another (stream -> message) keeps its place in the
    turn, so the swap doesn't change the layout."""
    for cls in ("turn-step", "turn-result"):
        widget.set_class(replaced.has_class(cls), cls)


def _highlight_default() -> bool:
    """Whether titles are syntax-highlighted at startup (``GPTME_TUI_DISPLAY_HIGHLIGHT``)."""
    return bool(get_config().get_env_bool("GPTME_TUI_DISPLAY_HIGHLIGHT", default=True))


def _show_hidden_default() -> bool:
    """Whether hidden messages are displayed at startup (``GPTME_TUI_DISPLAY_HIDDEN``).

    Hidden messages (e.g. token/time notices) are sent to the model but not
    normally shown.
    """
    return bool(get_config().get_env_bool("GPTME_TUI_DISPLAY_HIDDEN", default=False))


def _split_tool_calls(text: str) -> list[tuple[bool, str]]:
    """Split a text block into (is_toolcall, segment) pairs.

    Detects ``@tool_name(call_id): {...}`` lines emitted by the ``tool``
    format so they can be rendered as collapsible blocks instead of raw JSON.
    """
    segments: list[tuple[bool, str]] = []
    last_end = 0
    for m in _TOOL_CALL_RE.finditer(text):
        before = text[last_end : m.start()]
        if before.strip():
            segments.append((False, before))
        segments.append((True, m.group(0)))
        last_end = m.end()
    tail = text[last_end:]
    if tail.strip():
        segments.append((False, tail))
    return segments or [(False, text)]


def _tool_call_renderable(call_text: str) -> tuple[str, str, str]:
    """Parse a tool-format line into (title, code, lang) for a Collapsible."""
    import json  # local import — json not used elsewhere in this module

    m = _TOOL_CALL_RE.match(call_text.strip())
    if not m:
        return call_text, call_text, "text"
    tool_name = m.group(2)
    json_body = m.group(3)
    try:
        args = json.loads(json_body)
    except Exception:
        args = None
    if not isinstance(args, dict):
        return _tool_renderable(tool_name, None, json_body)
    if text := args.get("code") or args.get("command"):
        return _tool_renderable(tool_name, None, str(text))
    if tool_name == "patch_many":
        return _patch_many_renderable(args.get("patches"))
    path = args.get("path")
    text = args.get("content") or args.get("patch") or args.get("edit")
    if isinstance(text, str) and path:
        return _tool_renderable(tool_name, str(path), text)
    # unknown shape: title by path if any, expand to all arguments
    title, _, _ = _tool_renderable(tool_name, None, str(path or json_body))
    return title, json.dumps(args, indent=2), "json"


_PATCH_BLOCK_RE = re.compile(
    r"<<<<<<< ORIGINAL\n(.*?)\n?=======\n(.*?)\n?>>>>>>> UPDATED", re.DOTALL
)

# tools whose content is a file's text, highlighted by the file's type
_FILE_CONTENT_TOOLS = ("save", "append", "morph")


def _patch_as_diff(patch: str) -> str | None:
    """Conflict-marker patch (ORIGINAL/UPDATED blocks) as -/+ diff lines."""
    blocks = _PATCH_BLOCK_RE.findall(patch)
    if not blocks:
        return None
    lines: list[str] = []
    for original, updated in blocks:
        lines.append("@@")
        lines += [f"-{line}" for line in original.splitlines()]
        lines += [f"+{line}" for line in updated.splitlines()]
    return "\n".join(lines)


def _tool_renderable(
    tool_name: str, path: str | None, text: str
) -> tuple[str, str, str]:
    """(title, body, lang) for a tool call, shared by all tool formats.

    File tools are titled by path and show the file content (highlighted by
    file type) or, for patches, a -/+ diff; other tools show their code.
    """
    body, lang = (
        text.strip("\n"),
        ("python" if tool_name in ("ipython", "python") else "bash"),
    )
    summary = body
    if path and tool_name in _FILE_CONTENT_TOOLS:
        lang = Syntax.guess_lexer(path, body)
        summary = path
    elif path and tool_name == "patch":
        if diff := _patch_as_diff(text):
            body, lang = diff, "diff"
        else:
            lang = Syntax.guess_lexer(path, body)
        summary = path
    first_line = summary.split("\n")[0].strip()
    if len(first_line) > 55:
        first_line = first_line[:54] + "…"
    elif "\n" in summary.strip():
        first_line += " …"  # more lines follow
    title = f"▶ {tool_name}: {first_line}" if first_line else f"▶ {tool_name}"
    return title, body, lang


def _patch_many_renderable(patches: object) -> tuple[str, str, str]:
    """patch_many: one diff section per file."""
    import json

    items = patches
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return _tool_renderable("patch_many", None, str(items))
    if not isinstance(items, list):
        return _tool_renderable("patch_many", None, str(patches))
    sections, paths = [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        path, patch = str(item.get("path", "?")), str(item.get("patch", ""))
        paths.append(path)
        sections.append(f"--- {path}\n{_patch_as_diff(patch) or patch}")
    n = len(paths)
    if n == 0:
        return "▶ patch_many", "", "diff"
    title = f"▶ patch_many: {paths[0]}" + (f" (+{n - 1} more)" if n > 1 else "")
    return title, "\n".join(sections), "diff"


def _split_markdown_tool_calls(content: str) -> list[tuple[bool, str]]:
    """Split content into (is_tool, segment) pairs for markdown-format tool codeblocks.

    Uses Codeblock.iter_from_markdown to find tool codeblocks (identified by their
    language tag) and splits the raw content at those boundaries so they can be
    rendered as collapsible sections instead of inline code fences.
    """
    from ..codeblock import Codeblock
    from ..tools.base import ToolUse as _ToolUse

    # Collect (start_line, codeblock) pairs for tool-call codeblocks.
    tool_blocks: list[tuple[int, Codeblock]] = [
        (cb.start, cb)
        for cb in Codeblock.iter_from_markdown(content)
        if cb.start is not None and _ToolUse._from_codeblock(cb) is not None
    ]

    if not tool_blocks:
        return [(False, content)]

    lines = content.split("\n")
    segments: list[tuple[bool, str]] = []
    prose_lines: list[str] = []
    i = 0
    # Build a dict for O(1) lookup by start line
    blocks_by_line = dict(tool_blocks)

    while i < len(lines):
        cb = blocks_by_line.get(i)
        if cb is not None:
            if prose_lines:
                prose = "\n".join(prose_lines).strip()
                if prose:
                    segments.append((False, prose))
                prose_lines = []
            cb_text = f"{cb.fence}{cb.lang}\n{cb.content}\n{cb.fence}"
            segments.append((True, cb_text))
            # Skip: 1 opening-fence line + N content lines + 1 closing-fence line
            n_content = len(cb.content.splitlines()) if cb.content else 0
            closing_line_idx = i + 1 + n_content
            # When the closing fence is also an adjacent opening fence (e.g. ``````lang),
            # _extract_codeblocks normalizes it in-place and keeps start_line pointing at
            # that same raw line. Don't advance past it so the next block can be matched.
            adjacent_opening = (
                closing_line_idx < len(lines)
                and lines[closing_line_idx].startswith(cb.fence)
                and bool(
                    re.match(r"^`{3,}\S", lines[closing_line_idx][len(cb.fence) :])
                )
            )
            i = closing_line_idx if adjacent_opening else closing_line_idx + 1
        else:
            prose_lines.append(lines[i])
            i += 1

    if prose_lines:
        prose = "\n".join(prose_lines).strip()
        if prose:
            segments.append((False, prose))

    return segments or [(False, content)]


def _find_open_tool_fence_pos(text: str) -> int | None:
    """Return the char offset of the last unclosed tool-call fence, or None.

    Scans *text* line by line tracking open/closed code fences.  If the last
    unclosed fence carries a lang tag that is a supported tool (via
    ``is_supported_langtag``), its starting character offset is returned so
    the caller can split the prose before it from the still-streaming body.
    """
    from ..tools import is_supported_langtag

    lines = text.split("\n")
    pos = 0
    # stack of (char_pos, fence_chars, lang)
    open_fences: list[tuple[int, str, str]] = []

    for line in lines:
        stripped = line.rstrip()
        open_m = re.match(r"^(`{3,})(\S+)", stripped)
        close_m = re.match(r"^(`{3,})\s*$", stripped)
        if open_m:
            fchar, lang = open_m.group(1), open_m.group(2)
            open_fences.append((pos, fchar, lang))
        elif close_m and open_fences:
            if open_fences[-1][1] == close_m.group(1):
                open_fences.pop()
        pos += len(line) + 1  # +1 for '\n'

    # Return the outermost unclosed tool fence so nested fences (e.g. a shell
    # example inside a save block) don't split at the inner fence and expose
    # the outer block's raw content lines.
    for char_pos, _fchar, lang in open_fences:
        if is_supported_langtag(lang):
            return char_pos
    return None


def _split_streaming_markdown_tool_calls(text: str) -> list[tuple[bool, str]]:
    """Extends _split_markdown_tool_calls to also detect open (streaming) fences.

    After applying the standard closed-fence parser, inspects the last prose
    segment for an unclosed opening fence whose lang is a supported tool.  If
    found, the prose is split at the fence start so the partial tool body is
    returned as a tool segment (``is_tool=True``).  The caller renders it as a
    collapsed stub instead of raw lines.
    """
    segments = _split_markdown_tool_calls(text)

    # If the last segment is already a tool call there is nothing left to do.
    if segments and segments[-1][0]:
        return segments

    last_text = segments[-1][1] if segments else text
    open_pos = _find_open_tool_fence_pos(last_text)
    if open_pos is None:
        return segments

    before = last_text[:open_pos]
    tool_body = last_text[open_pos:]
    result = segments[:-1]
    if before.strip():
        result.append((False, before))
    result.append((True, tool_body))
    return result or [(False, text)]


def _streaming_tool_stub(tool_body: str) -> str:
    """One-line stub for a streaming (open/incomplete) markdown tool call."""
    first_line = tool_body.lstrip("\n").split("\n")[0].rstrip()
    if native := re.match(r"^@(\w+)\(", first_line):
        # native call: show its main argument (command/code/path), if any yet
        arg = _NATIVE_ARG_RE.search(first_line)
        summary = ""
        if arg:
            with contextlib.suppress(ValueError):
                summary = json.loads(f'"{arg.group(1)}"')
            summary = summary or arg.group(1)
            summary = summary.strip().split("\n")[0]
        if summary:
            summary = summary if len(summary) <= 55 else summary[:54] + "…"
            return f"▶ {native.group(1)}: {summary}…"
        return f"▶ {native.group(1)}…"
    m = re.match(r"^`{3,}(\S+)(?:\s+(.+?))?[ \t]*$", first_line)
    if not m:
        return "▶ tool…"
    full_lang, path = m.group(1), m.group(2)
    tool_name = full_lang.split(" ")[0]
    if path:
        summary = path if len(path) <= 55 else path[:54] + "…"
        return f"▶ {tool_name}: {summary}…"
    return f"▶ {tool_name}…"


def _streaming_all_segments(buffer: str) -> list[tuple[str, str]]:
    """Split *buffer* into (kind, text) pairs for the streaming preview.

    Kinds: ``"thinking"``, ``"tool_call"``, ``"prose"``.
    Empty/whitespace segments are omitted.
    """
    out: list[tuple[str, str]] = []
    for is_think, text in _split_thinking(buffer):
        if is_think:
            if text.strip():
                out.append(("thinking", text))
        else:
            for is_tool, seg in _split_streaming_markdown_tool_calls(text):
                if not seg.strip():
                    continue
                if is_tool:
                    out.append(("tool_call", seg))
                    continue
                for is_call, part in _split_streaming_native_tool_calls(seg):
                    if part.strip():
                        out.append(("tool_call" if is_call else "prose", part))
    return out


# A native ("tool" format) call while streaming: its header line, up to the
# end of that line or of the buffer (the JSON may still be arriving).
_STREAMING_NATIVE_CALL_RE = re.compile(r"^@\w+\([^)\n]*\):[^\n]*", re.MULTILINE)


def _split_streaming_native_tool_calls(text: str) -> list[tuple[bool, str]]:
    """Split prose into (is_tool, segment) at ``@tool(id): {...}`` lines."""
    out: list[tuple[bool, str]] = []
    last = 0
    for m in _STREAMING_NATIVE_CALL_RE.finditer(text):
        if text[last : m.start()].strip():
            out.append((False, text[last : m.start()]))
        out.append((True, m.group(0)))
        last = m.end()
    if text[last:].strip() or not out:
        out.append((False, text[last:]))
    return out


_NATIVE_ARG_RE = re.compile(r'"(?:command|code|path|file)"\s*:\s*"((?:[^"\\]|\\.)*)')


def _split_xml_tool_calls(content: str) -> list[tuple[bool, str]]:
    """Split content into (is_tool, segment) pairs for XML-format tool-call blocks."""
    segments: list[tuple[bool, str]] = []
    last_end = 0
    for m in _XML_TOOLUSE_RE.finditer(content):
        before = content[last_end : m.start()]
        if before.strip():
            segments.append((False, before))
        segments.append((True, m.group(0)))
        last_end = m.end()
    tail = content[last_end:]
    if tail.strip():
        segments.append((False, tail))
    return segments or [(False, content)]


def _markdown_tool_renderable(segment: str) -> tuple[str, str, str]:
    """Parse a markdown tool codeblock into (title, code, lang) for a Collapsible."""
    from ..codeblock import Codeblock

    cb = Codeblock.from_markdown(segment)
    tool_name, _, path = (cb.lang or "tool").partition(" ")
    return _tool_renderable(tool_name, path.strip() or None, cb.content)


def _xml_tool_renderables(segment: str) -> list[tuple[str, str, str]]:
    """Parse an XML tool-call block into (title, code, lang) tuples, one per invoke."""
    from ..tools.base import ToolUse as _ToolUse

    tool_uses = list(_ToolUse._iter_from_xml(segment))
    if not tool_uses:
        return [("▶ tool", segment, "xml")]
    return [
        _tool_renderable(tu.tool, tu.args[0] if tu.args else None, tu.content or "")
        for tu in tool_uses
    ]


def _split_all_tool_calls(text: str) -> list[tuple[bool, str, str]]:
    """Split *text* on all three tool-call formats in priority order.

    Returns ``(is_tool, fmt, segment)`` triples where ``fmt`` is ``"tool"``,
    ``"xml"``, ``"markdown"``, or ``"prose"``.  Non-tool prose from each
    parser is fed into the next parser, so mixed-format segments are handled
    correctly: e.g. a segment that contains both ``@tool`` calls and XML
    ``<function_calls>`` blocks renders all of them as collapsibles.
    """
    out: list[tuple[bool, str, str]] = []
    for is_t, seg1 in _split_tool_calls(text):
        if is_t:
            out.append((True, "tool", seg1))
        else:
            for is_x, seg2 in _split_xml_tool_calls(seg1):
                if is_x:
                    out.append((True, "xml", seg2))
                else:
                    for is_m, seg3 in _split_markdown_tool_calls(seg2):
                        if is_m:
                            out.append((True, "markdown", seg3))
                        elif seg3.strip():
                            out.append((False, "prose", seg3))
    return out or [(False, "prose", text)]


def _has_tool_calls(text: str) -> bool:
    """Return True if *text* contains any tool call in any supported format."""
    if _TOOL_CALL_RE.search(text) or _XML_TOOLUSE_RE.search(text):
        return True
    return any(is_tool for is_tool, _ in _split_markdown_tool_calls(text))


def _role_color_hex(role: str) -> str | None:
    """Configured ``[agent]``/``[user]`` color as hex, or None for the theme's."""
    return color_to_hex(configured_role_color(role))


def _apply_role_color(widget: Widget, role: str) -> None:
    """Color a message's role label and left border with the configured color."""
    if color := _role_color_hex(role):
        widget.styles.border_left = ("thick", color)
        widget.query_one(".role").styles.color = color


async def _after_refresh(widget: Widget) -> None:
    done: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def finish() -> None:
        if not done.done():
            done.set_result(None)

    widget.call_after_refresh(finish)
    await done


async def _rendered(widget: Widget, markdowns: Sequence[Awaitable[object]]) -> None:
    """Wait for Markdown widgets to render, then for the widget's height to
    settle: nested blocks (list items, tables) build their children later."""
    await asyncio.gather(*markdowns)
    height = -1
    while widget.outer_size.height != height:
        height = widget.outer_size.height
        await _after_refresh(widget)


def _stage(widget: Widget) -> None:
    """Keep a widget out of view (and out of the layout) until revealed.

    Not ``position: absolute``: an absolute child still counts towards its
    parent's auto height, reserving blank space.
    """
    widget.display = False


def _unstage(widget: Widget) -> None:
    widget.display = True


class ChatScroll(VerticalScroll):
    """The chat's scroll area, anchored at the bottom.

    Works around Textual (8.2) placing an anchored container's content for
    its previous height for one frame when the container resizes: the
    compositor reads ``container_size`` before updating it. When the input
    box shrinks (a multi-line prompt was sent), the chat grows and would
    show a blank band at the bottom for a frame. ``arrange()`` already
    receives the new size, so ``container_size`` reports that.
    """

    _arranged_height: int | None = None

    def arrange(self, size: Size, optimal: bool = False) -> DockArrangeResult:
        if not optimal:
            self._arranged_height = size.height
        return super().arrange(size, optimal)

    @property
    def container_size(self) -> Size:
        size = self._container_size
        if self._arranged_height is None:
            return size
        # arranged inside the scrollbars; container_size includes them
        return Size(size.width, self._arranged_height + self.scrollbar_size_horizontal)


class ChatMarkdown(Markdown):
    """Markdown that tells when its first render is done.

    Textual parses Markdown off-thread and mounts its blocks later, so a new
    Markdown widget shows blank space for a frame or more. Chat widgets stay
    hidden until theirs have rendered (see GptmeApp._mount_in_chat).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rendered = asyncio.Event()

    def on_markdown_table_of_contents_updated(
        self, event: Markdown.TableOfContentsUpdated
    ) -> None:
        # posted when an update (the first one included) has mounted its blocks
        if event.markdown is self:
            self.rendered.set()


class UserMessage(Vertical):
    """A user message, rendered with a distinct border."""

    def __init__(self, content: str, queued: bool = False):
        super().__init__(classes="message user" + (" queued" if queued else ""))
        self.content = content.strip()

    def compose(self) -> ComposeResult:
        label = _role_label("user")
        if "queued" in self.classes:
            label += " (queued)"
        yield Static(Text(label), classes="role")
        yield ChatMarkdown(self.content)

    def on_mount(self) -> None:
        _apply_role_color(self, "user")


def _highlight(code: str, lang: str) -> Text:
    """Syntax-highlight a snippet into one line of styled text."""
    text = Syntax(code, lang, theme="ansi_dark", word_wrap=True).highlight(code)
    text.rstrip()
    return text


_TITLE_RE = re.compile(r"([\w.-]+): (.*)", re.DOTALL)


def _tool_title(title: str, lang: str, highlight: bool, marker: bool = True) -> Text:
    """Tool-call title ``▶ tool: first line`` with the code highlighted.

    Collapsible draws its own ▶, so ``marker=False`` drops ours.
    """
    bare = title.removeprefix("▶ ")
    prefix = "▶ " if marker and bare != title else ""
    m = _TITLE_RE.fullmatch(bare)
    if not highlight or not m:
        return Text(prefix + bare)
    text = Text(prefix)
    text.append(f"{m.group(1)}:")
    text.append(" ")
    text.append_text(_highlight(m.group(2), lang))
    return text


def _summary_title(summary: str, highlight: bool) -> Text:
    """Output summary with `inline code` spans highlighted as shell."""
    parts = summary.split("`")
    if not highlight or len(parts) < 3:
        return Text(summary)
    text = Text()
    for i, part in enumerate(parts):
        # odd parts sit between backticks; an unmatched last one stays plain
        if i % 2 and i < len(parts) - 1:
            text.append_text(_highlight(part, "bash"))
        else:
            text.append(part)
    return text


class AssistantMessage(Vertical):
    """A completed assistant message, rendered as markdown."""

    def __init__(
        self, content: str, show_thinking: bool = False, highlight: bool = True
    ):
        super().__init__(classes="message assistant")
        self.content = content.strip()
        self.show_thinking = show_thinking
        self.highlight = highlight
        # under each tool call, where its results go in a grouped turn
        self.result_slots: list[CallResults] = []
        # (call index, message) of the results placed there
        self.results: list[tuple[int, Message]] = []
        self._call_ids: list[str | None] | None = None  # of the executed calls
        self._body = self._build_body()

    def on_mount(self) -> None:
        _apply_role_color(self, "assistant")

    def compose(self) -> ComposeResult:
        yield Static(Text(_role_label("assistant")), classes="role")
        yield from self._body

    def call_slot(
        self, call: int | None, call_id: str | None, tool_format: ToolFormat
    ) -> "CallResults | None":
        """Where results of one of the message's tool calls go: the call's
        index (execute_msg's), else its ID (logs from before the index), if
        the calls shown are the ones executed."""
        if self._call_ids is None:
            uses = ToolUse.iter_from_content(
                self.content, tool_format_override=tool_format
            )
            self._call_ids = [use.call_id for use in uses]
        ids = self._call_ids
        # tool-format calls mixed with markdown blocks are executed markdown
        # first, not in the order shown: no telling which call is which
        mixed = None in ids and any(ids)
        if mixed or len(ids) != len(self.result_slots):
            return None
        if call is None and call_id is not None and call_id in ids:
            call = ids.index(call_id)
        if call is not None and 0 <= call < len(self.result_slots):
            return self.result_slots[call]
        return None

    def _build_body(self) -> list[Widget]:
        think_segs = _split_thinking(self.content)
        has_thinking = any(is_think for is_think, _ in think_segs)
        has_tool_calls = any(
            not is_think and _has_tool_calls(text) for is_think, text in think_segs
        )

        if not has_thinking and not has_tool_calls:
            self.add_class("has-text")
            return [ChatMarkdown(self.content)]

        parts: list[tuple[str, Widget]] = []
        for is_think, text in think_segs:
            if is_think:
                thinking = Collapsible(
                    ChatMarkdown(text),
                    title="Thinking",
                    collapsed=not self.show_thinking,
                    classes="thinking-block",
                )
                parts.append(("thinking", thinking))
                continue
            for is_tool, fmt, seg in _split_all_tool_calls(text):
                if not is_tool:
                    if seg.strip():
                        parts.append(("prose", ChatMarkdown(seg)))
                    continue
                if fmt == "xml":
                    renderables = list(_xml_tool_renderables(seg))
                elif fmt == "tool":
                    renderables = [_tool_call_renderable(seg)]
                else:  # markdown
                    renderables = [_markdown_tool_renderable(seg)]
                for title, code, lang in renderables:
                    call = Collapsible(
                        Static(Syntax(code, lang, theme="ansi_dark", word_wrap=True)),
                        title=_tool_title(title, lang, self.highlight, marker=False),  # type: ignore[arg-type]
                        collapsed=True,
                        classes="tool-call-block",
                    )
                    parts.append(("tool", call))
        kinds = [kind for kind, _ in parts]
        # a later step that says something is set apart from the tool steps
        self.set_class("prose" in kinds, "has-text")
        body: list[Widget] = []
        for index, (kind, widget) in enumerate(parts):
            for cls in _prose_gaps(kinds, index):
                widget.add_class(cls)
            body.append(widget)
            if kind == "tool":
                slot = CallResults()
                self.result_slots.append(slot)
                body.append(slot)
        return body


class CallResults(Vertical):
    """A tool call's results, under the call in a grouped turn (empty and
    zero height otherwise)."""

    def __init__(self) -> None:
        super().__init__(classes="call-results")
        # placed before the slot was mounted (e.g. while loading history)
        self.pending: list[Widget] = []

    def compose(self) -> ComposeResult:
        yield from self.pending


def _prose_gaps(kinds: list[str], index: int) -> list[str]:
    """Space between the agent's own text and tool lines in a step, so the
    text stands out: a blank line between a tool call and text after it, and
    between text and a tool call after it. Thinking directly above the text
    needs none (see also ``.turn-step.has-text``)."""
    if kinds[index] != "prose":
        return []
    gaps = []
    if index > 0 and kinds[index - 1] == "tool":
        gaps.append("prose-gap-above")
    if index + 1 < len(kinds) and kinds[index + 1] != "prose":
        gaps.append("prose-gap-below")
    return gaps


_SYSTEM_TAG_RE = re.compile(r"\A<(system_\w+)>(.*)</\1>\Z", re.DOTALL)


def _system_display_text(content: str) -> str:
    """System message text for display, without a wrapping <system_*> tag.

    Hooks wrap notices (cwd changes, warnings) in tags meant for the model.
    """
    content = content.strip()
    if m := _SYSTEM_TAG_RE.match(content):
        return m.group(2).strip()
    return content


class SystemMessage(Vertical):
    """A system/tool-output message, collapsed by default (like <details>).

    Short one-line messages are shown as-is; long lines get a bounded summary.
    """

    def __init__(
        self, content: str, highlight: bool = True, expanded: bool = False
    ) -> None:
        super().__init__(classes="message system")
        self.content = _system_display_text(content)
        self.highlight = highlight
        self.expanded = expanded

    def compose(self) -> ComposeResult:
        single_line = "\n" not in self.content
        if single_line and len(self.content) <= SUMMARY_MAXLEN:
            yield Static(
                _summary_title(self.content, self.highlight), classes="system-line"
            )
            return
        yield Collapsible(
            Static(Text(self.content))
            if single_line
            else ChatMarkdown(_drop_summary_line(self.content)),
            title=_summary_title(_summarize(self.content), self.highlight),  # type: ignore[arg-type]
            collapsed=not self.expanded,
        )


class StreamProse(Vertical):
    """Streamed prose, double-buffered.

    Updating a Markdown widget in place re-parses it and rebuilds its blocks,
    which shows blank or half-built rows for a frame or more. Each update
    instead renders into a hidden widget that replaces the shown one in a
    single frame once ready; text arriving meanwhile is rendered next.
    """

    DEFAULT_CSS = "StreamProse { height: auto; }"

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._wanted = text
        self._shown: ChatMarkdown | None = None
        self._pending: ChatMarkdown | None = None

    @property
    def text(self) -> str:
        """The latest text, whether shown yet or not."""
        return self._wanted

    @property
    def shown(self) -> bool:
        """Whether any of its text is on screen yet."""
        return self._shown is not None

    def on_mount(self) -> None:
        self._render_next()

    def set_text(self, text: str) -> None:
        self._wanted = text
        if self._pending is None and self.is_mounted:
            self._render_next()

    def _render_next(self) -> None:
        if self._shown is not None and self._shown._markdown == self._wanted:
            return
        markdown = ChatMarkdown(self._wanted)
        _stage(markdown)
        self._pending = markdown
        self.mount(markdown)
        task = asyncio.create_task(self._swap_in(markdown))
        # held by the app (asyncio keeps only weak references to tasks), which
        # also lets GptmeApp.wait_rendered() wait for it
        tasks: set[asyncio.Task] | None = getattr(self.app, "_render_tasks", None)
        if tasks is not None:
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def _swap_in(self, markdown: ChatMarkdown) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                _rendered(markdown, [markdown.rendered.wait()]), REVEAL_TIMEOUT
            )
        if not self.is_attached:
            return
        with self.app.batch_update():
            _unstage(markdown)
            if self._shown is not None:
                self._shown.remove()
            elif isinstance(self.parent, StreamingMessage):
                # first text: replaces the "Generating…" line in this frame
                self.parent._sync_body(shown=True)
        self._shown, self._pending = markdown, None
        if markdown._markdown != self._wanted:
            self._render_next()


class StreamingMessage(Vertical):
    """Live view of the assistant response while tokens stream in."""

    def __init__(self, show_thinking: bool = True) -> None:
        super().__init__(classes="message assistant streaming")
        self._buffer = ""
        self.show_thinking = show_thinking
        self._body = Static(Text("Generating…"), classes="progress-placeholder")
        # Keep one row per thinking/prose segment, in final-render order.
        # Prose segments use Markdown widgets so heights match AssistantMessage at finalize.
        self._segments: list[Static | StreamProse] = []

    def on_mount(self) -> None:
        _apply_role_color(self, "assistant")
        if self._buffer:  # tokens that arrived before it was mounted
            # once mounted (is_mounted is still False in this handler)
            self.call_after_refresh(self._update_hidden_segments)

    def compose(self) -> ComposeResult:
        yield Static(Text(_role_label("assistant")), classes="role")
        yield self._body

    def _visible(self) -> str:
        return self._buffer if self.show_thinking else _strip_thinking(self._buffer)

    def _update_hidden_segments(self) -> None:
        if not self.is_mounted:
            return
        segments = _streaming_all_segments(self._buffer)
        if self.show_thinking:
            # Body mode: render full stream (thinking included) as plain text.
            content = self._buffer.strip()
            self._body.update(Text(content or "Generating…"))
            self._body.display = True
            # as in segment mode (_space_prose), so the finished message's
            # spacing matches
            self.set_class(
                any(kind == "prose" and text.strip() for kind, text in segments),
                "has-text",
            )
            with self.app.batch_update():
                for row in self._segments:
                    row.display = False
            return
        # Segment mode: collapse thinking into stubs; the body ("Generating…")
        # stays until a row is on screen (after the loop), so it never shrinks
        with self.app.batch_update():
            for index, (kind, text) in enumerate(segments):
                if index == len(self._segments):
                    # Use Markdown for prose so heights match AssistantMessage at finalize.
                    new_row: Static | StreamProse
                    if kind == "prose":
                        new_row = StreamProse(text.strip(), classes="stream-prose")
                    else:
                        css = "thinking-stub" if kind == "thinking" else "tool-stub"
                        new_row = Static(classes=css)
                    self._segments.append(new_row)
                    self.mount(new_row)
                row = self._segments[index]
                # If the kind changed (e.g. prose→thinking during streaming),
                # the existing widget type may not match. Replace it in place.
                needs_markdown = kind == "prose"
                type_ok = (needs_markdown and isinstance(row, StreamProse)) or (
                    not needs_markdown and isinstance(row, Static)
                )
                if not type_ok:
                    if needs_markdown:
                        replacement: Static | StreamProse = StreamProse(
                            text.strip(), classes="stream-prose"
                        )
                    else:
                        css = "thinking-stub" if kind == "thinking" else "tool-stub"
                        replacement = Static(classes=css)
                    row.remove()
                    anchor = self._segments[index - 1] if index > 0 else self._body
                    self.mount(replacement, after=anchor)
                    self._segments[index] = replacement
                    row = replacement
                row.set_class(kind == "thinking", "thinking-stub")
                row.set_class(kind == "tool_call", "tool-stub")
                row.set_class(kind == "prose", "stream-prose")
                if kind == "thinking":
                    assert isinstance(row, Static)
                    n = len(text.splitlines())
                    row.update(
                        Text(
                            f"▸ Thinking ({n} line{'s' if n != 1 else ''})",
                            style="dim",
                        )
                    )
                elif kind == "tool_call":
                    assert isinstance(row, Static)
                    row.update(Text(_streaming_tool_stub(text), style="dim"))
                else:
                    # Prose: Markdown renders the same as in AssistantMessage
                    if isinstance(row, StreamProse):
                        row.set_text(text.strip())
                    else:
                        row.update(Text(text.strip()))
            # Reuse existing rows; hide any the parser no longer emits.
            for index, row in enumerate(self._segments):
                row.display = index < len(segments)
            self._space_prose(segments)
            self._sync_body()

    def _space_prose(self, segments: list[tuple[str, str]]) -> None:
        """The same space around prose as the finished message gets
        (AssistantMessage), so replacing the stream doesn't move anything."""
        # whitespace-only prose isn't shown in the finished message
        shown = [
            (index, "tool" if kind == "tool_call" else kind)
            for index, (kind, text) in enumerate(segments)
            if kind != "prose" or text.strip()
        ]
        kinds = [kind for _, kind in shown]
        gaps = {
            index: _prose_gaps(kinds, position)
            for position, (index, _) in enumerate(shown)
        }
        self.set_class("prose" in kinds, "has-text")
        for index, row in enumerate(self._segments[: len(segments)]):
            row.set_class("prose-gap-above" in gaps.get(index, []), "prose-gap-above")
            row.set_class("prose-gap-below" in gaps.get(index, []), "prose-gap-below")

    def _sync_body(self, shown: bool = False) -> None:
        """Show the body ("Generating…") until a segment row is on screen."""
        if self.show_thinking:
            return  # body mode: the body shows the stream itself
        rows = self._segments[: len(_streaming_all_segments(self._buffer))]
        shown = shown or any(
            row.shown if isinstance(row, StreamProse) else True for row in rows
        )
        self._body.display = not shown

    def _refresh_thinking_display(self) -> None:
        """Sync widget visibility after show_thinking changes mid-stream."""
        self._update_hidden_segments()

    def append_token(self, token: str) -> None:
        self._buffer += token
        self._update_hidden_segments()

    def set_thinking(self, is_thinking: bool) -> None:
        """Update the placeholder when the model transitions in/out of thinking."""
        if not self._visible().strip():
            label = "Thinking…" if is_thinking else "Generating…"
            self._body.update(Text(label))


@dataclass
class _RunningTool:
    """A tool call in progress, for the running-tool line."""

    tool: str
    summary: str  # the command/args, one line
    started: float  # time.monotonic()
    # (seconds, what happens then): e.g. shell moving to the background
    limit: tuple[float, str] | None = None

    @classmethod
    def from_tool_use(cls, tool_use: ToolUse) -> "_RunningTool":
        content = tool_use.content or ""
        if not content and tool_use.kwargs:
            content = tool_use.kwargs.get("command") or " ".join(
                str(v) for v in tool_use.kwargs.values()
            )
        if not content and tool_use.args:
            content = " ".join(tool_use.args)
        return cls(
            tool=tool_use.tool,
            summary=_first_line(content),
            started=time.monotonic(),
            limit=_tool_limit(tool_use),
        )

    def render(self) -> Text:
        """One line, so the status line, its finished form and the stream
        that replaces it are all the same height (no jump when they swap).
        Timing first: a long command is cut short, not the timing."""
        elapsed = time.monotonic() - self.started
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(f"Running {self.tool}", style="bold")
        text.append(f" · {_duration(elapsed)}", style="dim")
        if self.limit:
            seconds, what = self.limit
            left = max(0.0, math.ceil(seconds - elapsed))
            text.append(f" · {what} in {_duration(left)}", style="dim")
        if self.summary:
            text.append(f"  {self.summary}")
        return text

    def render_finished(self, verb: str = "Ran") -> Text:
        """As render(), once done."""
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(f"{verb} {self.tool}", style="bold")
        text.append(f" · {_duration(time.monotonic() - self.started)}", style="dim")
        if self.summary:
            text.append(f"  {self.summary}")
        return text


def _first_line(content: str, maxlen: int = 100) -> str:
    """First line of a command, truncated, noting any further lines."""
    lines = content.strip().splitlines()
    if not lines:
        return ""
    first = lines[0] if len(lines[0]) <= maxlen else lines[0][: maxlen - 1] + "…"
    return first + (f" (+{len(lines) - 1} lines)" if len(lines) > 1 else "")


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _tool_limit(tool_use: ToolUse) -> tuple[float, str] | None:
    """When a running tool call stops waiting, if known (shell only)."""
    if tool_use.tool != "shell":
        return None
    if tool_use.kwargs and str(tool_use.kwargs.get("background", "")).lower() in (
        "true",
        "1",
    ):
        return None
    from ..tools.shell import _get_foreground_timeout, _get_timeout

    foreground = _get_foreground_timeout() if os.name == "posix" else None
    if foreground:
        return foreground, "moves to background"
    if timeout := _get_timeout():
        return timeout, "times out"
    return None


class ToolPlaceholder(Vertical):
    """Transient widget shown in the chat while a tool is running."""

    def __init__(self) -> None:
        super().__init__(classes="message system tool-placeholder")
        self._body = Static(Text("Running tool…"), classes="progress-placeholder")
        self.finished = False  # its tools have run

    def compose(self) -> ComposeResult:
        yield Static(Text("Tool"), classes="role")
        yield self._body

    def set_tool(self, tool_name: str) -> None:
        self._body.update(Text(f"Running {tool_name}…"))

    def set_running(self, text: Text) -> None:
        self._body.update(text)

    def set_finished(self, text: Text) -> None:
        self.finished = True
        self._body.update(text)


class InfoMessage(Static):
    """Dim informational line (help text, errors, hints)."""

    def __init__(self, content: str, error: bool = False):
        super().__init__(Text(content), classes="info" + (" error" if error else ""))


class CostMessage(Static):
    """Dim per-message cost line rendered by the Textual TUI."""

    def __init__(self, content: str):
        super().__init__(Text(content, style="dim"), classes="cost")


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


def renderables_for_message(
    msg: Message,
    expanded: bool = False,
    show_thinking: bool = True,
    highlight: bool = True,
) -> list:
    """Rich renderables for a message, for native-scrollback (inline) mode."""

    content = msg.content.strip()
    if msg.role == "user":
        return [
            Text(
                _role_label("user"),
                style=f"bold {configured_role_color('user') or 'green'}",
            ),
            Padding(RichMarkdown(content), (0, 0, 0, 2)),
            Text(),
        ]
    if msg.role == "assistant":
        color = configured_role_color("assistant") or "blue"
        items: list = [Text(_role_label("assistant"), style=f"bold {color}")]
        think_segs = _split_thinking(content)
        has_tool_calls = any(
            not is_think and _has_tool_calls(t) for is_think, t in think_segs
        )
        has_thinking = any(is_think for is_think, _ in think_segs)

        if not has_tool_calls and not has_thinking:
            items.append(Padding(RichMarkdown(content), (0, 0, 0, 2)))
        else:
            for is_think, text in think_segs:
                if is_think and not show_thinking:
                    items.append(Text(_thinking_stub(text), style="dim"))
                elif is_think:
                    items.append(
                        Panel(RichMarkdown(text), title="Thinking", expand=False)
                    )
                else:
                    for is_tool, fmt, seg in _split_all_tool_calls(text):
                        if not is_tool:
                            items.append(Padding(RichMarkdown(seg), (0, 0, 0, 2)))
                        elif fmt == "tool":
                            title, code, lang = _tool_call_renderable(seg)
                            items.append(
                                Panel(
                                    Syntax(
                                        code, lang, theme="ansi_dark", word_wrap=True
                                    ),
                                    title=_tool_title(title, lang, highlight),
                                    expand=False,
                                )
                            )
                        elif fmt == "xml":
                            for title, code, lang in _xml_tool_renderables(seg):
                                items.append(
                                    Panel(
                                        Syntax(
                                            code,
                                            lang,
                                            theme="ansi_dark",
                                            word_wrap=True,
                                        ),
                                        title=_tool_title(title, lang, highlight),
                                        expand=False,
                                    )
                                )
                        else:  # markdown
                            title, code, lang = _markdown_tool_renderable(seg)
                            items.append(
                                Panel(
                                    Syntax(
                                        code, lang, theme="ansi_dark", word_wrap=True
                                    ),
                                    title=_tool_title(title, lang, highlight),
                                    expand=False,
                                )
                            )
        items.append(Text())
        return items
    # system/tool output: compact summary line, optionally expanded
    content = _system_display_text(content)
    if "\n" not in content and len(content) <= SUMMARY_MAXLEN:
        line = _summary_title(content, highlight)
        line.stylize("dim")
        return [line, Text()]
    summary = Text("▶ ")
    summary.append_text(_summary_title(_summarize(content), highlight))
    summary.stylize("dim")
    renderables: list = [summary]
    if expanded:
        body = Text(content) if "\n" not in content else RichMarkdown(content)
        renderables.append(Padding(body, (0, 0, 0, 2)))
    renderables.append(Text())
    return renderables


# display-only settings toggled by /display (they never affect the model)
DISPLAY_SETTINGS = ("thinking", "outputs", "hidden", "highlight")
_ON = ("on", "show", "expanded", "true", "1")
_OFF = ("off", "hide", "collapsed", "false", "0")

# TUI-local commands (handled by the app, not the shared registry) and the
# completion candidates for each positional argument
TUI_COMMANDS: dict[str, list[list[str]]] = {
    "quit": [],
    "display": [list(DISPLAY_SETTINGS), ["on", "off"]],
}


def complete_path(text: str) -> list[str]:
    """Full-text candidates completing the last word as a filesystem path.

    Candidates are written so include_paths attaches them: directories get a
    trailing slash ("journ<Tab>" -> "journal/"; a bare "journal" is prose),
    bare cwd files without an extension get "./" ("./Makefile"), and names
    with spaces are backtick-quoted. The input widget keeps the cursor inside
    a quoted directory, so it can be submitted or completed further.
    """
    quoted = text.count("`") % 2 == 1  # completing inside an open `...
    seg = text[text.rindex("`") + 1 :] if quoted else re.split(r"\s", text)[-1]
    if not seg:
        return []
    at = "@" if seg.startswith("@") else ""
    dir_part, slash, prefix = seg[len(at) :].rpartition("/")
    dir_part += slash
    try:
        entries = list(os.scandir(os.path.expanduser(dir_part or ".")))
    except OSError:
        return []
    candidates = []
    for e in entries:
        if not e.name.startswith(prefix):
            continue
        if e.name.startswith(".") and not prefix.startswith("."):
            continue
        try:
            is_dir = e.is_dir()
        except OSError:  # e.g. a symlink loop
            continue
        path = at + dir_part + e.name + ("/" if is_dir else "")
        if not at and not dir_part and not is_dir and "." not in e.name.strip("."):
            path = "./" + path
        if quoted:
            path += "`"
        elif any(c.isspace() for c in path):
            path = f"`{path}`"
        candidates.append(text[: len(text) - len(seg)] + path)
    return sorted(candidates)


def complete_input(text: str) -> list[str]:
    """Full-line completion candidates: slash-commands (reusing the CLI
    command completers) or else a path for the last word."""

    if not text.startswith("/") or "/" in text.split(None, 1)[0][1:]:
        return complete_path(text)  # text, or an absolute path like /tmp/x
    parts = text.split(None, 1)
    if len(parts) == 1 and not text.endswith(" "):
        # completing the command name itself, else a root path like /tm
        commands = set(get_user_commands()) | {f"/{c}" for c in TUI_COMMANDS}
        return sorted(c for c in commands if c.startswith(text)) or complete_path(text)
    # completing command arguments
    arg_text = parts[1] if len(parts) > 1 else ""
    args = arg_text.split()
    partial = args[-1] if args and not arg_text.endswith(" ") else ""
    prev_args = args[:-1] if args and not arg_text.endswith(" ") else args
    base = text[: len(text) - len(partial)]
    if (positional := TUI_COMMANDS.get(parts[0][1:])) is not None:
        if len(prev_args) >= len(positional):
            return []
        choices = positional[len(prev_args)]
        return [base + c for c in choices if c.startswith(partial)]
    completer = get_command_completer(parts[0][1:])
    if completer is None:
        return [] if parts[0] in get_user_commands() else complete_path(text)
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


# Pastes at least this long collapse into a placeholder token in the input
PASTE_COLLAPSE_LINES = 3
PASTE_COLLAPSE_CHARS = 800


def _paste_label(n: int, text: str) -> str:
    lines = len(text.splitlines())
    size = f"+{lines} lines" if lines > 1 else f"{len(text)} chars"
    return f"[Pasted text #{n} {size}]"


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
        # placeholder token -> pasted text, expanded on submit; set before
        # TextArea's init, which already fires the selection watcher
        self._pastes: dict[str, str] = {}
        super().__init__(**kwargs)
        # steady cursor, like shells; hidden while the terminal is unfocused
        self.cursor_blink = False
        self._tab_candidates: list[str] = []
        self._tab_index = -1
        self._tab_last = ""
        self._tab_suffix = ""
        self._history_file = get_pt_history_file()
        self._history: list[str] = _load_pt_history(self._history_file)
        self._history_idx = -1  # -1 = not browsing; 0 = most recent
        self._history_saved = ""  # text buffered when browsing started
        self._history_edits: dict[str, str] = {}
        self._history_filter: list[str] = []  # prefix-filtered view; empty = unfiltered

    @property
    def _draw_cursor(self) -> bool:
        # the input keeps focus when the terminal loses it (see
        # GptmeApp._watch_app_focus), so hide the cursor then instead
        return super()._draw_cursor and self.app.app_focus

    def redraw_cursor(self) -> None:
        """Re-render after the terminal gains/loses focus.

        TextArea caches rendered lines, and with blinking off the cache key
        doesn't depend on focus, so a plain refresh would redraw the cursor
        from cache.
        """
        self._line_cache.clear()
        self.refresh()

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
        self._history_filter = []

    def _clear_completions(self) -> None:
        """Clear tab completion state and hide the overlay."""
        if self._tab_candidates:
            self._tab_candidates = []
            self._tab_index = -1
            self.post_message(self.CompletionsChanged([], -1))

    async def _on_paste(self, event: events.Paste) -> None:
        """Collapse long or multi-line pastes into a placeholder token."""
        text = event.text
        if (
            len(text.splitlines()) < PASTE_COLLAPSE_LINES
            and len(text) < PASTE_COLLAPSE_CHARS
        ):
            return  # Textual also dispatches TextArea's handler: plain insert
        event.stop()
        event.prevent_default()  # skip TextArea's insert
        # terminals send CR or CRLF line breaks in pastes; TextArea's insert
        # normalizes them, so the stored text must too
        text = re.sub(r"\r\n?", "\n", text)
        label = _paste_label(len(self._pastes) + 1, text)
        self._pastes[label] = text
        result = self.replace(label, *self.selection)
        self.move_cursor(result.end_location)

    def _expand_pastes(self, text: str) -> str:
        """Replace placeholder tokens still in *text* with the pasted text.

        One pass, so pasted text that happens to contain a label is not
        itself expanded.
        """
        if not self._pastes:
            return text
        pattern = "|".join(re.escape(label) for label in self._pastes)
        return re.sub(pattern, lambda m: self._pastes[m.group()], text)

    def _paste_spans(self, row: int) -> list[tuple[int, int]]:
        """Column spans of placeholder tokens on *row*."""
        line = self.document.get_line(row)
        spans = []
        for label in self._pastes:
            start = line.find(label)
            while start != -1:
                spans.append((start, start + len(label)))
                start = line.find(label, start + len(label))
        return spans

    def watch_selection(self, previous: Selection, selection: Selection) -> None:
        """Keep the cursor out of placeholder tokens, so each acts as one unit.

        A cursor that lands inside a token snaps to its far edge in the
        direction of travel (nearest edge when the move came from elsewhere).
        """
        row, col = selection.end
        for start, end in self._paste_spans(row):
            if start < col < end:
                prev_row, prev_col = previous.end
                if prev_row == row and prev_col <= start:
                    col = end
                elif prev_row == row and prev_col >= end:
                    col = start
                else:
                    col = start if col - start <= end - col else end
                # a plain cursor move stays collapsed; a selection keeps its anchor
                anchor = (row, col) if selection.is_empty else selection.start
                self.selection = Selection(anchor, (row, col))
                return

    def edit(self, edit: Edit) -> EditResult:
        """Widen deletions and replacements to cover whole placeholder tokens.

        Every edit path (Backspace/Delete, word and line deletes, cutting a
        selection) goes through here, so no edit can leave half a token,
        which could no longer be expanded.
        """
        top, bottom = sorted((edit.from_location, edit.to_location))
        if top != bottom:
            for start, end in self._paste_spans(top[0]):
                if start < top[1] < end:
                    top = (top[0], start)
            for start, end in self._paste_spans(bottom[0]):
                if start < bottom[1] < end:
                    bottom = (bottom[0], end)
            edit = Edit(edit.text, top, bottom, edit.maintain_selection_offset)
        return super().edit(edit)

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self._clear_completions()
            self.post_message(self.Submitted(self._expand_pastes(self.text)))
            self._pastes.clear()
            return
        if event.key == "ctrl+d" and not self.text:
            # like a shell: Ctrl+D quits on empty input, else deletes forward
            event.stop()
            event.prevent_default()
            await self.run_action("app.quit")
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
                history = self._history_filter or self._history
                if self._history_idx == -1:
                    self._history_saved = self.text
                    # Build prefix-filtered view on first Up press
                    prefix = self.text
                    self._history_filter = (
                        [e for e in self._history if e.startswith(prefix)]
                        if prefix
                        else []
                    )
                    history = self._history_filter or self._history
                else:
                    orig = history[-(self._history_idx + 1)]
                    self._history_edits[orig] = self.text
                new_idx = self._history_idx + 1
                if new_idx < len(history):
                    self._history_idx = new_idx
                    orig = history[-(new_idx + 1)]
                    self._set_text(self._history_edits.get(orig, orig))
                return
        if event.key == "down":
            lines = self.text.split("\n")
            row, _ = self.cursor_location
            if row == len(lines) - 1 and self._history_idx >= 0:
                event.stop()
                event.prevent_default()
                history = self._history_filter or self._history
                orig = history[-(self._history_idx + 1)]
                self._history_edits[orig] = self.text
                new_idx = self._history_idx - 1
                if new_idx < 0:
                    self._history_idx = -1
                    self._history_filter = []
                    self._set_text(self._history_saved)
                else:
                    self._history_idx = new_idx
                    orig = history[-(new_idx + 1)]
                    self._set_text(self._history_edits.get(orig, orig))
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
        if "\n" in self.text and is_message_command(self.text):
            return  # commands are single-line
        if self._tab_candidates and self.text == self._tab_last:
            # repeated Tab: cycle through candidates
            self._tab_index = (self._tab_index + 1) % len(self._tab_candidates)
            self._set_text(self._tab_candidates[self._tab_index])
        else:
            cursor = len(self.document.get_text_range((0, 0), self.cursor_location))
            before, after = self.text[:cursor], self.text[cursor:]
            # Complete inside a closed quoted reference without duplicating its
            # closing quote. Other input retains full-text completion behavior.
            quoted = before.count("`") % 2 and after.startswith("`")
            self._tab_suffix = after[1:] if quoted else ""
            if quoted:
                candidates = complete_input(before)
            else:
                candidates = complete_input(self.text)
            if not candidates:
                return
            # Compute the common prefix before adding the untouched suffix:
            # otherwise differing paths truncate the closing quote and prose.
            prefix = os.path.commonprefix(
                [candidate.removesuffix("`") for candidate in candidates]
                if quoted
                else candidates
            )
            if quoted:
                prefix += "`"
            candidates = [candidate + self._tab_suffix for candidate in candidates]
            prefix += self._tab_suffix
            self._tab_candidates = candidates
            self._tab_index = -1
            if len(candidates) == 1:
                self._set_text(candidates[0])
                self._tab_candidates = []
            elif prefix and prefix != self.text:
                self._set_text(prefix)
            else:
                self._tab_index = 0
                self._set_text(candidates[0])
        completed = (
            self.text[: -len(self._tab_suffix)] if self._tab_suffix else self.text
        )
        if (
            self._tab_suffix
            or completed.endswith("/`")
            or (
                self._tab_candidates
                and self._tab_index == -1
                and completed.endswith("`")
            )
        ):
            # Stay at the completed reference, not at the end of trailing prose.
            lines = completed.removesuffix("`").split("\n")
            self.move_cursor((len(lines) - 1, len(lines[-1])))
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
        content = preview or (tool_use.preview_content if tool_use else "") or ""
        self.preview_content = content
        self.lang = _get_lang_for_tool(self.tool_name, content)

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(
                Text(f"Execute {self.tool_name}?", style="bold"), id="confirm-title"
            )
            # key hints go above the preview: a tall preview can push the
            # dialog past the (height-capped) inline region and clip its end
            yield Static(
                Text("[y] execute   [n] skip   [a] auto-confirm session"),
                id="confirm-help",
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

    def action_confirm(self) -> None:
        self.dismiss(ConfirmationResult.confirm())

    def action_skip(self) -> None:
        self.dismiss(ConfirmationResult.skip("Declined by user"))

    def action_auto(self) -> None:
        cast("GptmeApp", self.app).auto_confirm = True
        self.dismiss(ConfirmationResult.confirm())


class UrlConfirmScreen(ModalScreen):
    """Modal asking the user to confirm fetching URLs found in their message."""

    BINDINGS = [
        Binding("y,enter", "confirm", "Fetch URL(s)"),
        Binding("n,escape", "skip", "Skip"),
    ]

    def __init__(self, urls: list[str]) -> None:
        super().__init__()
        self.urls = urls

    def compose(self) -> ComposeResult:
        title = "Fetch URL?" if len(self.urls) == 1 else f"Fetch {len(self.urls)} URLs?"
        body = (
            "\n".join(self.urls)
            if len(self.urls) == 1
            else "\n".join(f"{i + 1}. {url}" for i, url in enumerate(self.urls))
        )
        with Vertical(id="confirm-dialog"):
            yield Static(Text(title, style="bold"), id="confirm-title")
            yield Static(
                Text("[y/enter] fetch   [n/esc] skip"),
                id="confirm-help",
            )
            with VerticalScroll(id="confirm-preview"):
                yield Static(body)

    def action_confirm(self) -> None:
        self.dismiss(self.urls)

    def action_skip(self) -> None:
        self.dismiss([])


@dataclass
class _CommandResult:
    """Outcome of a registry command, handed back to the UI thread."""

    handled: bool = False
    output: str = ""
    error: str | None = None
    exit: bool = False


# Seconds between statusline refreshes when nothing else triggers one.
STATUSLINE_REFRESH = 10

# Max stream re-renders per second; each re-lays out the whole buffer.
STREAM_FPS = 20
# /display settings that re-render the chat (deferred while a turn runs)
REBUILDING_SETTINGS = ("hidden", "highlight", "grouping")
# Longest a new chat widget stays hidden waiting for its Markdown to render:
# a backstop (rendering takes a few frames), generous for loaded CI machines.
REVEAL_TIMEOUT = 3.0


class _SequentialPrompt(str):
    """A queued command-line prompt: sent when the previous turn ends (like
    the CLI), never joined into a running turn."""


class GptmeApp(App):
    """gptme TUI: streaming chat with live input, queueing and collapsible output."""

    TITLE = "gptme"

    CSS = """
    Screen {
        background: ansi_default;
    }
    Screen:inline {
        height: auto;
        max-height: 40%;
        /* Textual pads the inline region with blank lines above and below;
           the full-screen layout has neither */
        border-top: none;
        border-bottom: none;
    }
    #live {
        height: auto;
        max-height: 8;
        margin: 0 1;
        background: ansi_default;
    }
    #chat {
        padding: 0 1;
        background: ansi_default;
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
    .message.system > .system-line {
        color: $text-muted;
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
    /* a tool call is one line like the thinking line above it: no border
       row (▔▔▔), padding row, extra indent or box (Collapsible defaults) */
    .tool-call-block {
        border: none;
        padding: 0;
        background: transparent;
    }
    .thinking-block CollapsibleTitle {
        color: $text-muted;
    }
    /* tool calls and their outputs recede like thinking: the agent's own
       text is what to read */
    .tool-call-block CollapsibleTitle, .message.system CollapsibleTitle {
        color: $text-muted;
        text-style: none;
    }
    /* full margins: a margin-bottom rule would reset another rule's top */
    .message .prose-gap-above {
        margin: 1 0 0 0;
    }
    .message .prose-gap-below {
        margin: 0 0 1 0;
    }
    .message .prose-gap-above.prose-gap-below {
        margin: 1 0 1 0;
    }
    .thinking-block Markdown {
        color: $text-muted;
        margin-bottom: 1;
    }
    /* compact markdown: the widget defaults add blank lines around
       codeblocks (MarkdownFence margin 1 0) and after the last block */
    .message Markdown {
        padding: 0;
        background: transparent;
    }
    /* headers: bold in the text color, one blank line above and none below
       (Textual's defaults: 2 above, 1 below, a blue underlined H2, a
       centered H1 on a background) */
    .message MarkdownHeader {
        margin: 1 0 0 0;
        color: $text;
        background: transparent;
        text-style: bold;
        content-align: left middle;
    }
    .message MarkdownHeader:first-child {
        margin-top: 0;
    }
    .message Static {
        background: transparent;
    }
    .message > .progress-placeholder {
        color: $text-muted;
    }
    /* the tool status is one line however long the command: cut, not
       wrapped (a taller line would make the chat jump when replaced) */
    .tool-placeholder > .progress-placeholder {
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    /* grouped turns (group_turns): steps after the first continue the block
       without a name or blank line; tool results and the running tool sit
       indented under the step, in the agent's bar */
    .message.turn-step, .message.turn-result {
        margin-top: 0;
    }
    /* a later step with text: a blank line sets it apart from the tool steps
       before it (steps that only think and call tools stay compact) */
    .message.turn-step.has-text {
        padding: 1 0 0 1;  /* padding, not margin: the bar continues */
    }
    .message.turn-step > .role, .message.turn-result > .role {
        display: none;
    }
    .message.turn-result {
        border-left: thick $primary;
        padding-left: 3;
    }
    .call-results {
        height: auto;
    }
    /* a result under its call, in the step's bar (no bar of its own) */
    .call-results > .message.turn-result {
        border-left: none;
        padding-left: 2;
    }
    .message MarkdownFence {
        margin: 0;
    }
    /* expanded sections: body directly under the title */
    .message Collapsible > Contents {
        padding: 0 0 0 3;
    }
    /* the fence's inner label pads a blank line above and below */
    .message MarkdownFence > Label {
        padding: 0 1;
    }
    .message MarkdownBlock:last-child {
        margin-bottom: 0;
    }
    .info {
        color: $text-muted;
        margin: 1 0 0 1;
    }
    .cost {
        color: $text-muted;
        margin: 0 0 0 2;
    }
    .info.error {
        color: $error;
    }
    #bottom {
        dock: bottom;
        height: auto;
        background: ansi_default;
    }
    #queued {
        height: auto;
        max-height: 5;
        margin: 0 1;
        color: $text-muted;
        background: ansi_default;
    }
    /* input between two horizontal rules, prompt marker on the left */
    #input-row {
        height: auto;
        margin-top: 1;
        border-top: solid $foreground 25%;
        border-bottom: solid $foreground 25%;
        background: ansi_default;
    }
    #prompt-marker {
        width: 2;
        background: ansi_default;
    }
    #input {
        border: none;
        padding: 0;
        height: auto;
        max-height: 10;
        background: ansi_default;
    }
    #input .text-area--cursor-line {
        background: transparent;
    }
    /* status lines up with the input text, key hints on the right */
    #status-row {
        height: auto;
        max-height: 4;
        margin: 0 2;
        background: ansi_default;
    }
    #status-row Static {
        color: $text-muted;
        background: ansi_default;
    }
    #status {
        width: 1fr;
    }
    #keys {
        width: auto;
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
        Binding(
            "ctrl+o", "toggle_details", "Expand/collapse outputs+thinking", show=True
        ),
    ]

    def __init__(
        self,
        manager: LogManager,
        tool_format: ToolFormat = "markdown",
        workspace: Path | None = None,
        auto_confirm: bool = False,
        inline: bool = False,
        experimental_jelly_errors: bool = False,
        initial_prompts: list[str] | None = None,
    ):
        super().__init__()
        # Keep Textual's truecolor theme, but preserve ANSI default through the
        # final output filter so terminal foreground/background remain native.
        self._filters = [
            filter_
            for filter_ in self._filters
            if not isinstance(filter_, ANSIToTruecolor)
        ]
        self.manager = manager
        self.tool_format: ToolFormat = tool_format
        self.workspace = workspace or manager.workspace
        self.auto_confirm = auto_confirm
        self.inline = inline
        self.experimental_jelly_errors = experimental_jelly_errors
        # prompts from the command line, submitted once mounted (see on_mount)
        self._initial_prompts = list(initial_prompts or [])
        self._stream_text = ""
        # Snapshot the caller's context (default model, output format, …) so
        # worker threads see it — gptme stores this state in ContextVars,
        # which fresh threads do not inherit. Workers run sequentially
        # (exclusive=True), so reusing one Context is safe, and mutations
        # (e.g. /model-style changes) persist across turns.
        self._chat_ctx = contextvars.copy_context()
        # Own one CostTracker window for this TUI run so command admission
        # and generation workers share the same tracking_id.
        # kept for the status bar, which can't enter _chat_ctx mid-generation
        self._session_costs = self._chat_ctx.run(
            CostTracker.ensure_session, session_id_for_logdir(self.manager.logdir)
        )
        self._skill_session_id = str(uuid4())
        self._active_skill_invocation_id: str | None = None
        # [tui] statusline: a command rendering the status bar (see statusline.py)
        self._statusline_cmd = get_config().user.tui.statusline
        self._statusline_stats = LogStats()
        self._statusline_running = False
        self._statusline_pending: dict | None = None
        self._statusline_output: str | None = None
        self._started = time.monotonic()
        self.prompt_queue: list[str | Message] = []
        # URLs confirmed for fetching when a prompt was queued, by id(Message)
        self._confirmed_urls: dict[int, list[str]] = {}
        self._queued_widgets: list[Widget] = []
        # a queued prompt's widget, popped from the queue and waiting to be
        # replaced in place by the user message it becomes
        self._delivered_widget: Widget | None = None
        self.generating = False
        self.state = "idle"
        self._interrupt_event = threading.Event()
        # see _interrupt_step()
        self._generation_thread: int | None = None
        self._step_lock = threading.Lock()
        self._in_step = False
        self._step_processes: set[int] = set()  # started during the step
        self._quitting = False
        self._stream_widget: StreamingMessage | None = None
        # Streamed chunks are buffered by the worker and rendered at most
        # STREAM_FPS times a second: each render re-lays out the whole buffer.
        self._stream_lock = threading.Lock()
        self._stream_pending: list[str] = []
        self._stream_flush_scheduled = False
        self._stream_last_flush = 0.0
        self._tool_placeholder: ToolPlaceholder | None = None
        # chat widgets wait hidden until rendered; revealed in mount order
        self._reveal_tail: asyncio.Task | None = None
        # streamed prose updates rendering off-screen (StreamProse)
        self._render_tasks: set[asyncio.Task] = set()
        # the tool call running now (set by the TOOL_EXECUTE_PRE hook)
        self._running_tool: _RunningTool | None = None
        self._running_tool_timer: Timer | None = None
        # assistant message rendered from the stream when its tools started,
        # replaced by the final message once they finish
        self._provisional_assistant: Widget | None = None
        # True when _on_before_tool_execution has already rendered the reply so
        # _on_step_message must not render the same message again.
        self._message_pre_rendered = False
        self._outputs_expanded = False
        self.show_thinking = _show_thinking_default()
        self.show_hidden = _show_hidden_default()
        self.highlight = _highlight_default()
        self.group_turns = _group_turns_default()
        self._rebuild_pending = False  # see _rebuild_chat
        # notices shown meanwhile (not in the log): re-shown after the rebuild
        self._notices_since_pending: list[tuple[str, bool]] = []
        # the last chat item's place in the turn (see _place_in_turn)
        self._flow_prev: str | None = None
        # the turn's latest step, whose tool calls take their results
        self._turn_step: Widget | None = None
        self._stdio_sink: IO[str] | None = None
        self._real_stdout: IO[str] | None = None
        self._real_stderr: IO[str] | None = None
        self._term_attrs: list | None = None
        # tracked separately for the status bar: the authoritative value
        # lives in a ContextVar inside _chat_ctx, which the UI thread
        # cannot enter while a worker is running
        self._model: ModelMeta | None = None
        # set by /restart [cli|web]; main() re-execs (gptme-tui, or gptme for
        # "cli") or opens restart_web_url after the app has exited
        self.restart_requested = False
        self.restart_target: Interface | None = None
        self.restart_web_url: str | None = None
        # id of the /restart web check running in a worker, if any; only the
        # current one may act, and new input cancels it
        self._web_restart_seq = 0
        self._web_restart_pending: int | None = None
        # True while our SIGWINCH handler is installed (inline mode only)
        self._inline_sigwinch_installed = False

    # ------------------------------------------------------------------ UI

    def compose(self) -> ComposeResult:
        if self.inline:
            # native-scrollback mode: no in-app chat view; completed messages
            # are printed into the terminal's scrollback via _print_above
            yield Static(id="live")
            # queued prompts stay visible here until dispatched: printing them
            # into scrollback would duplicate them once they are submitted
            yield Static(id="queued")
            yield from self._compose_input()
            return
        yield ChatScroll(id="chat")
        with Vertical(id="bottom"):
            yield Static("", id="completions")
            yield from self._compose_input()

    def _compose_input(self) -> ComposeResult:
        with Horizontal(id="input-row"):
            yield Static(Text("❯"), id="prompt-marker")
            yield ChatInput(id="input")
        with Horizontal(id="status-row"):
            yield Static(id="status")
            yield Static(
                Text("Enter send · Ctrl+J newline · Ctrl+O details"), id="keys"
            )

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
        # show each tool call as it starts: step() yields the assistant message
        # only after all of its tools have finished
        register_hook(
            name="tui_tool_start",
            hook_type=HookType.TOOL_EXECUTE_PRE,
            func=self._tui_tool_start_hook,
        )
        if self.inline:
            self._install_inline_resize_handler()
        else:
            # inline mode prints history to the terminal before the app starts
            self._render_history()
            chat = self.query_one("#chat", VerticalScroll)
            # prevent the chat scroll area from stealing focus; input is always
            # the active widget for keyboard input
            chat.can_focus = False
            self.watch(chat, "virtual_size", self._anchor_chat, init=False)
            self.watch(chat, "size", self._anchor_chat, init=False)
        self.query_one("#input", ChatInput).focus()
        self._update_status()
        # watchdog: tools can reset the tty at any point during execution
        self.set_interval(0.5, self._restore_terminal)
        if self._statusline_cmd:
            # durations and other time-based fields change without events
            self.set_interval(STATUSLINE_REFRESH, self._update_status)
        if self._initial_prompts:
            self.call_after_refresh(self._submit_initial_prompts)

    def _enqueue_prompt(self, prompt: str | Message) -> None:
        """Queue a prompt and, in full-screen mode, show it as a queued widget."""
        self.prompt_queue.append(prompt)
        if not self.inline:
            widget = UserMessage(_queued_prompt_text(prompt), queued=True)
            self._queued_widgets.append(widget)
            self._mount_in_chat(widget, at_end=True)
        self._update_status()

    def _take_steer_prompts(self) -> list[Message]:
        """Queued prompts that can join the running turn at its next step.

        Taken in order, stopping at the first that must wait for the turn to
        end: a slash-command, a prompt with unconfirmed URLs, a queued skill
        invocation (it starts its own turn), or a command-line prompt (those
        run one turn each, like the CLI).
        """
        taken: list[Message] = []
        while self.prompt_queue and not self._interrupt_event.is_set():
            prompt = self.prompt_queue[0]
            if isinstance(prompt, Message):
                # queued with its URLs confirmed, or a skill invocation
                if id(prompt) not in self._confirmed_urls:
                    break
                urls = self._confirmed_urls.pop(id(prompt))
            elif (
                isinstance(prompt, _SequentialPrompt)
                or is_message_command(prompt)
                or extract_urls(prompt)
            ):
                break
            else:
                prompt, urls = Message("user", prompt, quiet=True), []
            self._pop_queued_prompt()
            msg = include_paths(prompt, self.workspace, pre_confirmed_urls=urls)
            self._show_message(msg)
            taken.append(msg)
        if taken:
            self._drop_delivered_widget()
            if not self.inline:  # inline mode has no chat widget
                self._move_tool_line_below_delivered()
            self._update_status()
        return taken

    def _move_tool_line_below_delivered(self) -> None:
        """Delivered prompts sit where they were queued, below the tool
        status line; the next step's output (which replaces that line)
        belongs after them."""
        chat = self.query_one("#chat", VerticalScroll)
        placeholder = self._tool_placeholder
        if placeholder is None or placeholder.parent is not chat:
            return
        insert = self._queued_widgets[0] if self._queued_widgets else None
        if insert is not None and insert.parent is chat:
            chat.move_child(placeholder, before=insert)
        else:
            chat.move_child(placeholder, after=chat.children[-1])

    def _pop_queued_prompt(self) -> str | Message:
        """Take the next queued prompt. Its widget stays until the user
        message it becomes replaces it in place (see _show_message)."""
        prompt = self.prompt_queue.pop(0)
        self._drop_delivered_widget()
        if self._queued_widgets:
            self._delivered_widget = self._queued_widgets.pop(0)
        return prompt

    def _drop_delivered_widget(self) -> None:
        """Remove a popped prompt's widget that no user message replaced
        (e.g. it was a command)."""
        if (widget := self._delivered_widget) is not None:
            self._delivered_widget = None
            widget.remove()

    async def _submit_user_input(self, prompt: str | Message) -> None:
        """Submit a prompt, or run it as a slash-command like typed input."""
        if not isinstance(prompt, Message) and is_message_command(prompt):
            self._drop_delivered_widget()  # commands aren't shown as messages
            self._handle_command(prompt)
            if self.generating or self._quitting or not self.prompt_queue:
                return
            await self._submit_user_input(self._pop_queued_prompt())
            return
        await self._submit(prompt)

    async def _submit_initial_prompts(self) -> None:
        """Submit command-line prompts like the CLI: the first now, the rest
        queued, each sent when the previous turn finishes."""
        first, *rest = self._initial_prompts
        self._initial_prompts = []
        for prompt in rest:
            self._enqueue_prompt(
                _SequentialPrompt(prompt) if isinstance(prompt, str) else prompt
            )
        await self._submit_user_input(first)

    async def on_event(self, event: events.Event) -> None:
        # A click can land on a widget removed since the last frame (e.g. the
        # "Generating…" placeholder on interrupt). Textual would start a text
        # selection in its parent, which is None, and crash: drop the click.
        if isinstance(event, events.MouseDown) and not self.mouse_captured:
            widget, _ = self.screen.get_widget_and_offset_at(event.x, event.y)
            if widget is not None and widget is not self.screen and not widget.parent:
                return
        await super().on_event(event)

    def on_unmount(self) -> None:
        self._quitting = True
        self._restore_inline_resize_handler()
        self._interrupt_event.set()
        abandon_skill_invocations(self.manager.logdir, self._skill_session_id)
        record_skill_phase(
            self.manager.logdir, self._active_skill_invocation_id, "abandoned"
        )
        unregister_hook("tui_confirm", HookType.TOOL_CONFIRM)
        unregister_hook("tui_tool_start", HookType.TOOL_EXECUTE_PRE)
        # The saved streams are Textual's stdout/stderr captures, and unmount
        # can run after Textual has already restored the real streams:
        # only undo our own redirect, or later prints go to a dead capture.
        if self._real_stdout is not None and sys.stdout is self._stdio_sink:
            sys.stdout = self._real_stdout
        if self._real_stderr is not None and sys.stderr is self._stdio_sink:
            sys.stderr = self._real_stderr
        if self._stdio_sink is not None:
            self._stdio_sink.close()
            self._stdio_sink = None

    def _install_inline_resize_handler(self) -> None:
        """Replace the inline driver's SIGWINCH handler.

        Textual's inline driver clears the whole visible screen (``ESC[2J``)
        on resize, blanking the native scrollback shown above the app. Clear
        only the app region instead. After the terminal reflows a narrower
        width, fragments of the old region may remain above; that is
        preferable to wiping the visible transcript.
        """
        if not hasattr(signal, "SIGWINCH") or self._driver is None:
            return
        loop = asyncio.get_running_loop()

        def on_resize(signum, frame) -> None:
            if self._quitting:
                return
            try:
                loop.call_soon_threadsafe(self._on_inline_resize)
            except RuntimeError:
                # loop closed between unmount and SIG_DFL restore
                return

        signal.signal(signal.SIGWINCH, on_resize)
        self._inline_sigwinch_installed = True

    def _restore_inline_resize_handler(self) -> None:
        """Drop our SIGWINCH handler so post-exit resizes don't hit a dead app.

        Textual's Linux inline driver already resets SIGWINCH to SIG_DFL in
        ``disable_input()``, which runs before Unmount. Restoring the previous
        handler here would reinstall Textual's dead callback. Tests use the
        headless driver, whose ``disable_input`` is a no-op, so we must reset
        ourselves. SIG_DFL is the right post-exit default in both cases.
        """
        if not self._inline_sigwinch_installed or not hasattr(signal, "SIGWINCH"):
            return
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        self._inline_sigwinch_installed = False

    def _on_inline_resize(self) -> None:
        if self._quitting:
            return
        driver = self._driver
        if driver is None:
            return
        width, height = driver._get_terminal_size()  # type: ignore[attr-defined]
        # Between frames the cursor is parked at the caret offset from the
        # region origin (see _print_above); move to the origin and clear down.
        # Region lines are full-width, so on a narrower terminal each one the
        # terminal reflowed now spans several rows.
        # Textual parks this on App after the first inline frame. SIGWINCH can
        # fire before that, so match _print_above and don't assume it exists.
        caret = getattr(self, "_previous_cursor_position", None)
        if caret is None:
            caret = Offset(0, 0)
        old_width = self.size.width
        rows_per_line = -(-old_width // width) if 0 < width < old_width else 1
        up = caret.y * rows_per_line + (caret.x // width if width else 0)
        sequence = "\r"
        if up:
            sequence += Control.move(0, -up).segment.text
        driver.write(sequence + "\x1b[J")
        self._previous_cursor_position = Offset(0, 0)
        size = Size(width, height)
        self.post_message(events.Resize(size, size))

    def end_session(self) -> None:
        """Run SESSION_END hooks (e.g. the cost summary), like the CLI on exit.

        Called after the app has exited, so hook output reaches the terminal.
        """

        def run_hooks() -> None:
            if msgs := trigger_hook(
                HookType.SESSION_END,
                logdir=self.manager.logdir,
                manager=self.manager,
            ):
                for msg in msgs:
                    self.manager.append(msg)

        try:
            # cost tracking and other session state live in the chat context
            self._chat_ctx.run(run_hooks)
        except RuntimeError:
            # a generation thread that outlived the app still holds the context
            logger.warning("Skipping session-end hooks: generation still running")
        self.manager.write(sync=True)

    def _render_history(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        msgs = [m for m in self.manager.log if self.show_hidden or not m.hide]
        if len(msgs) > MAX_INITIAL_MESSAGES:
            chat.mount(
                InfoMessage(
                    f"… {len(msgs) - MAX_INITIAL_MESSAGES} earlier messages not shown"
                )
            )
            msgs = msgs[-MAX_INITIAL_MESSAGES:]
        self._flow_prev = None
        self._turn_step = None
        for msg in msgs:
            widget = self._widget_for(msg)
            if not widget:
                continue
            slot = self._place_in_turn(widget, _flow_kind(msg), msg)
            if slot is None:
                chat.mount(widget)
            elif slot.is_attached:
                slot.mount(widget)
            else:
                slot.pending.append(widget)
        chat.anchor(False)  # re-evaluated for the new content
        self.call_after_refresh(self._anchor_chat)

    def _anchor_chat(self, *_: object) -> None:
        """Anchor the chat to the bottom once its content overflows.

        Anchored, it stays at the bottom as content grows or shrinks, without
        a scroll per update, until the user scrolls up (scrolling back down
        re-anchors). Not before overflowing: Textual would then bottom-align
        a short chat instead of starting it at the top.
        """
        chat = self.query_one("#chat", VerticalScroll)
        height = chat.container_size.height
        if not chat.is_anchored and 0 < height < chat.virtual_size.height:
            chat.anchor()

    def _widget_for(self, msg: Message) -> Widget | None:
        if msg.role == "user":
            return UserMessage(msg.content)
        if msg.role == "assistant":
            return AssistantMessage(
                msg.content, show_thinking=self.show_thinking, highlight=self.highlight
            )
        if msg.role == "system":
            return SystemMessage(
                msg.content, highlight=self.highlight, expanded=self._outputs_expanded
            )
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
        if msg.hide and not self.show_hidden:  # e.g. token/time notices
            return
        cost_text = inline_cost_text(msg) if msg.role == "assistant" else None
        if self.inline:
            renderables = renderables_for_message(
                msg,
                self._outputs_expanded,
                show_thinking=self.show_thinking,
                highlight=self.highlight,
            )
            if cost_text:
                renderables.insert(-1, Text(cost_text, style="dim"))
            self._print_above(*renderables)
            return
        widget = self._widget_for(msg)
        slot = self._place_in_turn(widget, _flow_kind(msg), msg) if widget else None
        delivered = self._delivered_widget if msg.role == "user" else None
        if delivered is not None:
            # a queued prompt becoming the message: take its place
            self._delivered_widget = None
            if widget:
                self._mount_in_chat(widget, replaces=delivered)
            else:
                delivered.remove()
        elif widget:
            self._mount_in_chat(widget, into=slot)
        if cost_text:
            self._mount_in_chat(CostMessage(cost_text))

    def _place_in_turn(
        self, widget: Widget, kind: str, msg: Message | None = None
    ) -> "CallResults | None":
        """Group an agent turn into one block (``group_turns``).

        A step continuing the turn drops its name and the blank line above;
        a tool result goes indented under the tool call it came from (the
        running tool's line under the step), in the turn's bar. Anything
        else (hook output, notices) keeps its own block and ends the
        grouping, so items never leave log order: a result after hook output
        isn't pulled up.

        Returns the slot under the call to mount a result in, None for the
        chat.
        """
        prev, self._flow_prev = self._flow_prev, kind
        if kind == "step":
            self._turn_step = widget
        if not self.group_turns or prev not in ("step", "result"):
            if kind == "result":
                # shown on its own (e.g. after hook output): the block is
                # interrupted, so the next step shows the agent's name again
                self._flow_prev = "other"
            return None
        if kind == "step":
            widget.add_class("turn-step")
        elif kind == "result":
            widget.add_class("turn-result")
            step = self._turn_step
            if isinstance(step, AssistantMessage) and msg is not None:
                call, call_id = _call_index(msg), msg.call_id
                if slot := step.call_slot(call, call_id, self.tool_format):
                    step.results.append((step.result_slots.index(slot), msg))
                    return slot
            # e.g. the running tool's line: under the step
            if color := _role_color_hex("assistant"):
                widget.styles.border_left = ("thick", color)
        return None

    def _take_turn_place(self, widget: Widget, replaced: Widget) -> None:
        """``widget`` replaces ``replaced`` (stream -> message) in its turn."""
        _keep_turn_place(widget, replaced)
        if self._turn_step is replaced:
            self._turn_step = widget

    def _show_info(self, text: str, error: bool = False) -> None:
        if self._rebuild_pending:
            self._notices_since_pending.append((text, error))
        if self.inline:
            self._print_above(Text(text, style="red" if error else "bright_black"))
            return
        widget: Widget
        if error and self.experimental_jelly_errors:
            widget = BouncingError(text)
        else:
            widget = InfoMessage(text, error=error)
        self._place_in_turn(widget, "other")
        self._mount_in_chat(widget)

    def _mount_in_chat(
        self,
        widget: Widget,
        *,
        before: Widget | None = None,
        replaces: Widget | None = None,
        at_end: bool = False,
        into: "CallResults | None" = None,
    ) -> None:
        """Add a widget to the chat once it has rendered, never half-built.

        Markdown renders a frame or more after mounting, showing blank space
        meanwhile. The widget is mounted hidden and shown when its Markdown
        is ready, after any widget mounted before it; ``replaces`` is removed
        in that same frame, so a swap never shows a gap or a jump.

        Placement: the conversation grows above the running tool's status
        line, and both stay above queued prompts (``at_end``), which wait at
        the bottom until delivered. A tool result can go ``into`` the slot
        under its call instead.
        """
        if into is not None and not into.is_attached:
            into.pending.append(widget)  # shown with the message it is in
            return
        chat = self.query_one("#chat", VerticalScroll)
        if before is None:
            before = replaces
        if before is None and not at_end and into is None:
            before = self._chat_insert_point()
        _stage(widget)
        if into is not None:
            mounted = into.mount(widget)
        elif before is not None:
            mounted = chat.mount(widget, before=before)
        else:
            mounted = chat.mount(widget)
        previous = self._reveal_tail
        self._reveal_tail = asyncio.create_task(
            self._reveal(widget, mounted, previous, replaces)
        )

    def _hold_input_height(self) -> None:
        """Keep the input box's height until what was submitted shows up.

        Clearing a multi-line input shrinks it at once, growing the chat,
        while the submitted message appears a few frames later (once
        rendered): the chat would slide down and back. Both change in the
        same frame instead (_reveal), or after REVEAL_TIMEOUT.
        """
        chat_input = self.query_one("#input", ChatInput)
        chat_input.styles.min_height = chat_input.outer_size.height
        self.set_timer(REVEAL_TIMEOUT, self._release_input_height)

    def _release_input_height(self) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#input", ChatInput).styles.min_height = None

    async def wait_rendered(self) -> None:
        """Wait until chat widgets and streamed text added so far are shown
        (and what they replace removed)."""
        while True:
            pending = [t for t in self._render_tasks if not t.done()]
            if (tail := self._reveal_tail) is not None and not tail.done():
                pending.append(tail)
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    def _chat_insert_point(self) -> Widget | None:
        """Where new conversation content goes: above the tool status line,
        else above the queued prompts; None for the end of the chat."""
        chat = self.query_one("#chat", VerticalScroll)
        placeholder = self._tool_placeholder
        if placeholder is not None and placeholder.parent is chat:
            return placeholder
        queued = [w for w in self._queued_widgets if w.parent is chat]
        return queued[0] if queued else None

    async def _reveal(
        self,
        widget: Widget,
        mounted: Awaitable[object],
        previous: "asyncio.Task | None",
        replaces: Widget | None,
    ) -> None:
        try:
            if previous is not None:
                with contextlib.suppress(Exception):
                    await previous
            await mounted
            markdowns = [md.rendered.wait() for md in widget.query(ChatMarkdown)]
            if markdowns:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(_rendered(widget, markdowns), REVEAL_TIMEOUT)
        finally:
            with self.batch_update():
                if widget.is_attached:
                    _unstage(widget)
                self._release_input_height()
                if replaces is not None and replaces.is_attached:
                    replaces.remove()

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
            fd = sys.__stdin__.fileno()  # type: ignore[union-attr]
            if termios.tcgetattr(fd) != self._term_attrs:
                termios.tcsetattr(fd, termios.TCSANOW, self._term_attrs)

    def _update_status(self, run_statusline: bool = True) -> None:
        parts = []
        model = self._model
        tokens: int | None = None
        if model:
            parts.append(model.full)
            try:
                tokens = len_tokens(self.manager.log.messages, model.model)
                pct = 100 * tokens / model.context if model.context else 0
                parts.append(f"{tokens // 1000}k/{model.context // 1000}k ({pct:.0f}%)")
            except Exception:  # token counting must never break the UI
                pass
        cost = sum(e.cost for e in self._session_costs.snapshot_entries())
        if cost:
            parts.append(f"${cost:.2f}")
        parts.append(self.state)
        if self.prompt_queue:
            parts.append(f"{len(self.prompt_queue)} queued")
        if self._statusline_cmd and run_statusline:
            self._statusline_stats.update(self.manager.log.messages)
            self._request_statusline(
                build_payload(
                    session=self.manager.logdir.name,
                    logdir=self.manager.logdir,
                    workspace=self.workspace,
                    model=model.full if model else None,
                    context_size=model.context if model else None,
                    tokens=tokens,
                    cost=cost,
                    duration_ms=int((time.monotonic() - self._started) * 1000),
                    state=self.state,
                    queued=len(self.prompt_queue),
                    stats=self._statusline_stats,
                )
            )
        if not self._statusline_output:
            self.query_one("#status", Static).update(Text(" | ".join(parts)))
        if self.inline:
            self._update_queued_view()

    def _update_queued_view(self) -> None:
        """Show pending prompts above the input (inline mode)."""
        # the view is capped at MAX_QUEUED_SHOWN lines (CSS max-height)
        shown = self.prompt_queue
        if len(shown) > MAX_QUEUED_SHOWN:
            shown = shown[: MAX_QUEUED_SHOWN - 1]
        lines = [
            f"⏳ queued: {_summarize(_queued_prompt_text(item), maxlen=100)}"
            for item in shown
        ]
        if hidden := len(self.prompt_queue) - len(shown):
            lines.append(f"   … +{hidden} more queued")
        text = Text("\n".join(lines))
        queued = self.query_one("#queued", Static)
        queued.update(text)
        queued.display = bool(self.prompt_queue)

    def _request_statusline(self, payload: dict) -> None:
        """Run the statusline command off the UI thread; while one runs, keep
        only the latest payload, sent when it finishes."""
        if self._statusline_running:
            self._statusline_pending = payload
            return
        self._statusline_running = True
        self.run_worker(
            functools.partial(self._statusline_worker, payload),
            thread=True,
            group="statusline",
        )

    def _statusline_worker(self, payload: dict) -> None:
        output = run_statusline(self._statusline_cmd or "", payload, self.workspace)
        if not self._quitting:
            self.call_from_thread(self._show_statusline, output)

    def _show_statusline(self, output: str | None) -> None:
        self._statusline_running = False
        self._statusline_output = output
        try:
            self.query_one("#status", Static)
        except NoMatches:  # shutting down: the screen is gone
            return
        if output:
            self.query_one("#status", Static).update(Text.from_ansi(output))
        else:
            self._update_status(run_statusline=False)  # the built-in status
        if (payload := self._statusline_pending) is not None:
            self._statusline_pending = None
            self._request_statusline(payload)

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

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.value.strip()
        chat_input = self.query_one("#input", ChatInput)
        self._hold_input_height()
        chat_input.text = ""
        if text:
            chat_input._push_history(text)
            if self._web_restart_pending is not None:
                # new input wins over a /restart web still being checked
                self._web_restart_pending = None
                self._show_info("Cancelled /restart web: new input was submitted.")
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

        if is_message_command(text):
            # a real /command; paths like /tmp/foo.md fall through to _submit,
            # which attaches file contents via include_paths
            self._handle_command(text)
            return
        if self.generating and extract_urls(text):
            # confirm URL fetching now, not when the queued prompt is sent
            self.push_screen(
                UrlConfirmScreen(extract_urls(text)),
                callback=lambda confirmed: self._enqueue_confirmed(text, confirmed),
            )
        elif self.generating:
            self._enqueue_prompt(text)
        else:
            await self._submit(text)

    def _enqueue_confirmed(self, text: str, confirmed: list[str] | None) -> None:
        """Queue a prompt whose URLs were confirmed (or declined) at queue time."""
        msg = Message("user", text, quiet=True)
        self._confirmed_urls[id(msg)] = confirmed or []
        if self.generating:
            self._enqueue_prompt(msg)
        elif self._interrupt_event.is_set():
            # the turn was interrupted meanwhile: hand the text back, as for
            # the rest of the queue, instead of starting a new turn
            self._confirmed_urls.pop(id(msg))
            chat_input = self.query_one("#input", ChatInput)
            existing = chat_input.text.strip()
            chat_input._set_text(f"{existing}\n{text}" if existing else text)
        else:  # the turn ended while the dialog was open
            self.call_later(self._submit, msg)

    def _can_resume(self) -> bool:
        last = next((m for m in reversed(self.manager.log) if not m.hide), None)
        return last is not None and last.role != "assistant"

    # commands that take over the terminal (e.g. spawn $EDITOR), which would
    # corrupt the TUI display
    UNSUPPORTED_COMMANDS = frozenset({"edit"})

    def _request_exit(self) -> None:
        """Request a clean exit, marking us as quitting first.

        ``_quitting`` gates queued-prompt draining and worker callbacks, so it
        must be set before ``exit()``: otherwise a prompt queued behind a
        ``/quit`` or ``/restart`` command would still be submitted (and even
        start a generation) while the app is shutting down.
        """
        self._quitting = True
        self.exit()

    def _handle_command(self, text: str) -> None:
        """Run a slash-command through the CLI command registry."""

        cmd = text.split()[0].lstrip("/")
        if cmd in ("quit", "q"):  # TUI-local alias for /exit
            self._request_exit()
            return
        if cmd == "display":  # TUI-local: display only, allowed while working
            self._display_command(text.split()[1:])
            return
        if cmd in self.UNSUPPORTED_COMMANDS:
            self._show_info(
                f"/{cmd} takes over the terminal and is not supported in the "
                "TUI; switch to the CLI with /restart cli to use it."
            )
            return
        if self.generating:
            self._show_info(
                "Commands can't run while the agent is working; retry when idle."
            )
            return
        if cmd == "restart":
            # The shared command prompts on stdin and execs in place, which
            # can't work under Textual: exit cleanly, main() re-execs (or
            # opens the web UI). Check the target first so a failure keeps
            # the session open.
            try:
                target = parse_restart_target(text.split()[1:])
            except RestartError as e:
                self._show_info(str(e))
                return
            if target == "web":
                # network checks: off the event loop so the UI stays responsive
                self._show_info("Looking for a gptme-server…")
                self._web_restart_seq += 1
                self._web_restart_pending = self._web_restart_seq
                self.run_worker(
                    functools.partial(self._web_restart_worker, self._web_restart_seq),
                    thread=True,
                    group="restart",
                )
                return
            self._request_restart("cli" if target == "cli" else None, None)
            return

        msg = Message("user", text, quiet=True)
        before = self.manager.log.messages
        self.manager.append(msg)
        # Off the event loop: commands like /compact call the LLM and would
        # freeze the UI. Input queues meanwhile, as during generation.
        self.generating = True
        self._interrupt_event.clear()  # only a Ctrl+C during this command counts
        self._set_state(f"running /{cmd}…")
        self.run_worker(
            functools.partial(self._command_worker, msg, cmd, before),
            thread=True,
            group="command",
        )

    def _command_worker(self, msg: Message, cmd: str, before: list[Message]) -> None:
        result = self._execute_command(msg, cmd)
        # from (a copy of) the chat context, like the generation worker: the
        # callback, and widgets it mounts (e.g. the chat re-rendered after
        # /compact), run in the context it's sent from, and this thread's own
        # is empty: config would be a default one, without the agent's name.
        # A copy: the callback enters _chat_ctx itself (a context can't be
        # entered twice at once).
        self._chat_ctx.copy().run(
            self.call_from_thread, self._background_command_done, cmd, before, result
        )

    def _background_command_done(
        self, cmd: str, before: list[Message], result: _CommandResult
    ) -> None:
        self.generating = False
        if self._quitting:
            return
        if self._rebuild_pending:
            self._rebuild_chat()
        self._set_state("idle")
        self._finish_command(cmd, before, result)
        if not self.generating and not self._quitting:
            # e.g. /skill:<name> already started a turn, which dispatches later
            self.call_later(self._dispatch_queue)

    def _execute_command(self, msg: Message, cmd: str) -> _CommandResult:
        """Run a registry command, capturing output. Thread-safe: no UI calls."""
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
                def execute_owned_command() -> bool:
                    with skill_invocation_owner(
                        self.manager.logdir, self._skill_session_id
                    ):
                        return execute_cmd(msg, self.manager)

                handled = self._chat_ctx.run(execute_owned_command)
        except SystemExit:  # /exit
            return _CommandResult(exit=True)
        except (EOFError, RuntimeError) as e:
            # interactive prompts hit the empty stdin (EOFError), or fail on
            # an event loop; other RuntimeErrors are real failures
            if isinstance(e, RuntimeError) and "event loop" not in str(e):
                logger.exception("Command failed")
                return _CommandResult(error=f"Command failed: {e}")
            return _CommandResult(
                error=f"/{cmd} needs interactive input, which the TUI doesn't "
                f"support; pass arguments directly (e.g. /{cmd} <args>) or "
                "use the CLI."
            )
        except Exception as e:
            logger.exception("Command failed")
            return _CommandResult(error=f"Command failed: {e}")
        finally:
            sys.stdin = real_stdin
        return _CommandResult(handled=handled, output=buf.getvalue().strip())

    def _finish_command(
        self, cmd: str, before: list[Message], result: _CommandResult
    ) -> None:
        if result.exit:
            self._request_exit()
            return
        if result.error:
            self._show_info(result.error, error=True)
            return
        self._model = self._chat_ctx.run(get_default_model)
        # Sync tool format — /model may have called set_tool_format()
        self.tool_format = self._chat_ctx.run(get_tool_format)
        if self.manager.log.messages != before:
            # command changed the log (undo, appended messages, …): re-render
            self._rebuild_chat()
        if result.output:
            self._show_info(result.output)
        if not result.handled:
            logger.warning("Command %r not handled by registry", cmd)
            self._show_info(f"Unknown command: /{cmd}")
        else:
            # Commands like /skill:<name> queue a follow-up user prompt.
            # Drain it and start a turn, matching the CLI chat loop.
            self._drain_command_queued_prompts()
        self._update_status()

    def _display_command(self, args: list[str]) -> None:
        """``/display [thinking|outputs|hidden|highlight|grouping] [on|off]``: display-only settings.

        No setting lists the current state; no value toggles.
        """
        usage = "Usage: /display [thinking|outputs|hidden|highlight|grouping] [on|off]"
        # name -> (current value, setter, on/off wording)
        settings = {
            "thinking": (self.show_thinking, self._set_thinking, ("shown", "hidden")),
            "outputs": (
                self._outputs_expanded,
                self._set_outputs,
                ("expanded", "collapsed"),
            ),
            "hidden": (
                self.show_hidden,
                self._set_hidden,
                ("messages shown", "messages hidden"),
            ),
            "highlight": (self.highlight, self._set_highlight, ("on", "off")),
            "grouping": (self.group_turns, self._set_group_turns, ("on", "off")),
        }
        if not args:
            state = ", ".join(
                f"{name}: {words[0] if value else words[1]}"
                for name, (value, _, words) in settings.items()
            )
            self._show_info(f"{state}\n{usage}")
            return
        name = args[0].lower()
        value = args[1].lower() if len(args) > 1 else None
        if name not in settings or len(args) > 2:
            self._show_info(usage, error=True)
            return
        current, setter, words = settings[name]
        if value is None:
            on = not current
        elif value in _ON:
            on = True
        elif value in _OFF:
            on = False
        else:
            self._show_info(usage, error=True)
            return
        setter(on)
        note = " (applies to new output)" if self.inline else ""
        if name == "grouping" and self.inline:
            note = " (full-screen mode only: inline output isn't grouped)"
        elif self.generating and not self.inline and name in REBUILDING_SETTINGS:
            note = " (new output now; earlier messages when this turn ends)"
        self._show_info(f"{name.capitalize()} {words[0 if on else 1]}{note}.")

    def _set_group_turns(self, on: bool) -> None:
        """Grouped: an agent turn is one block, tool results under their step.
        Flat: one block per log message (closer to the log; for debugging)."""
        self.group_turns = on
        self._rebuild_chat()

    def _set_hidden(self, on: bool) -> None:
        self.show_hidden = on
        self._rebuild_chat()

    def _set_highlight(self, on: bool) -> None:
        self.highlight = on
        self._rebuild_chat()

    def _refocus_input_after_toggle(self) -> None:
        # Clicking a title focuses it (drawn bold) and takes keyboard focus
        # from the input; hand it back so typing keeps working.
        if not isinstance(self.screen, ModalScreen):
            self.query_one("#input").focus()

    # Textual posts the Toggled subclasses, whose handler names differ
    def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
        self._refocus_input_after_toggle()

    def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:
        self._refocus_input_after_toggle()

    def _set_thinking(self, on: bool) -> None:
        self.show_thinking = on
        if self._stream_widget is not None:
            self._stream_widget.show_thinking = on
            self._stream_widget._refresh_thinking_display()
        if not self.inline:
            for block in self.query(".thinking-block").results(Collapsible):
                block.collapsed = not on

    def _drain_command_queued_prompts(self) -> None:
        """Turn durable prompts queued by a command into a TUI user turn.

        Skill commands write to the durable prompt queue instead of yielding a
        message. The CLI drains that queue at the top of its chat loop; without
        this, the prompt sits until a CLI session opens the same log.
        """
        drained = drain_prompt_queue(self.manager.logdir)
        if not drained:
            return
        first, *rest = drained
        self.manager.append(first)
        self._show_message(first)
        # Keep drained Message objects so later batched skill prompts retain
        # skill_invocation_id instead of being rebuilt from content strings.
        self.prompt_queue.extend(rest)
        self._start_generation()

    def _rebuild_chat(self) -> None:
        if self.inline:
            # scrollback can't be rewritten; the change is noted via output
            return
        if self.generating:
            # the live stream/tool widgets would be removed from under the
            # running turn: rebuild once it ends (_generation_done)
            self._rebuild_pending = True
            return
        notices, self._notices_since_pending = self._notices_since_pending, []
        self._rebuild_pending = False
        chat = self.query_one("#chat", VerticalScroll)
        chat.remove_children()
        self._render_history()
        self._remount_queued_prompts()
        # e.g. the error that ended the turn: not in the log, keep it shown
        for text, error in notices:
            self._show_info(text, error=error)

    def _remount_queued_prompts(self) -> None:
        """Queued prompts live only in the TUI, not the log.

        Commands that rebuild the chat (``/model``, ``/display highlight``, …)
        would otherwise drop the waiting widgets while the queue still runs.
        """
        self._queued_widgets.clear()
        for prompt in self.prompt_queue:
            widget = UserMessage(_queued_prompt_text(prompt), queued=True)
            self._queued_widgets.append(widget)
            self._mount_in_chat(widget, at_end=True)

    async def _submit(self, prompt: str | Message) -> None:
        if isinstance(prompt, Message):
            msg = prompt
            text = msg.content
        else:
            text = prompt
            msg = Message("user", text, quiet=True)
        # Confirm before fetching any URLs, matching CLI behavior (TUI can't
        # use prompt_toolkit's sync dialog inside the running event loop).
        # A callback, not push_screen_wait: callers aren't workers (#3351).
        urls = extract_urls(text)
        if isinstance(prompt, Message) and id(prompt) in self._confirmed_urls:
            # confirmed when it was queued
            self._send(msg, self._confirmed_urls.pop(id(prompt)))
        elif urls:
            self.push_screen(
                UrlConfirmScreen(urls),
                callback=lambda confirmed: self._send(msg, confirmed),
            )
        else:
            self._send(msg, None)

    def _send(self, msg: Message, pre_confirmed_urls: list[str] | None) -> None:
        msg = include_paths(msg, self.workspace, pre_confirmed_urls=pre_confirmed_urls)
        self.manager.append(msg)
        self._show_message(msg)
        self._start_generation()

    # --------------------------------------------------------- generation

    def _start_generation(self) -> None:
        logger.debug("starting generation worker")
        # Snapshot the submitted prompt before hooks or generation mutate the log.
        prompt = next((m for m in reversed(self.manager.log) if m.role == "user"), None)
        self._active_skill_invocation_id = (
            prompt.metadata.get("skill_invocation_id")
            if prompt and prompt.metadata
            else None
        )
        self.generating = True
        self._interrupt_event.clear()
        self._set_state("generating")
        self.run_worker(
            self._generation_worker, thread=True, exclusive=True, group="generation"
        )

    def _web_restart_worker(self, request: int) -> None:
        """Thread worker: check the web UI can take over, then exit to it."""
        try:
            url = prepare_web_switch(self.manager.logdir.name)
        except RestartError as e:
            self.call_from_thread(self._finish_web_check, request, str(e))
            return
        self.call_from_thread(self._finish_web_check, request, None, url)

    def _finish_web_check(
        self, request: int, error: str | None, url: str | None = None
    ) -> None:
        """Back on the UI thread: act on the web check if it's still current."""
        if self._web_restart_pending != request:
            return  # cancelled by new input, or superseded by a newer check
        self._web_restart_pending = None
        if error is not None:
            self._show_info(error)
        else:
            self._request_restart(None, url)

    def _request_restart(self, target: Interface | None, web_url: str | None) -> None:
        """Exit so main() re-execs into ``target`` (or opens ``web_url``)."""
        if self.generating or len(self.screen_stack) > 1:
            # e.g. a prompt started while the web check ran, or a dialog
            # (tool/URL confirmation) is open: don't drop it by exiting
            self._show_info("Not restarting: the agent is busy; retry when idle.")
            return
        self.manager.write(sync=True)
        self.restart_requested = True
        self.restart_target = target
        self.restart_web_url = web_url
        self._request_exit()

    def _generation_worker(self) -> None:
        """Thread worker: run the step loop inside the captured chat context."""
        self._generation_thread = threading.get_ident()
        self._chat_ctx.run(self._generation_body)

    def _tui_tool_start_hook(
        self, data: ToolExecutePreData
    ) -> Generator[Message, None, None]:
        """TOOL_EXECUTE_PRE, on the worker thread: show the starting tool call."""
        # only this TUI's own turn, not e.g. a thread-mode subagent's tools
        if data.tool_use is not None and (
            threading.get_ident() == self._generation_thread
        ):
            running = _RunningTool.from_tool_use(data.tool_use)
            with contextlib.suppress(Exception):  # never block the tool
                self.call_from_thread(self._on_tool_start, running)
        yield from ()

    def _on_tool_start(self, running: _RunningTool) -> None:
        if not self.inline and self._stream_widget is not None:
            # render the streamed response now, not after its tools finish;
            # the last chunks go straight into the final message: rendering
            # them in the stream widget about to be replaced would re-lay it
            # out mid-swap (its height wobbles while Markdown re-parses)
            with self._stream_lock:
                pending = "".join(self._stream_pending)
                self._stream_pending.clear()
                self._stream_flush_scheduled = False
            content = self._stream_widget._buffer + pending
            self._finalize_stream_in_place(Message("assistant", content))
            self._provisional_assistant = self._last_assistant_widget()
        else:
            self._flush_stream()
        self._running_tool = running
        self._set_state(f"running {running.tool}")
        self._show_tool_placeholder()
        self._tick_running_tool()
        if self._running_tool_timer is None:
            self._running_tool_timer = self.set_interval(1, self._tick_running_tool)

    def _last_assistant_widget(self) -> Widget | None:
        assistants = self.query_one("#chat", VerticalScroll).query(AssistantMessage)
        return assistants.last() if assistants else None

    def _tick_running_tool(self) -> None:
        if (running := self._running_tool) is None:
            return
        text = running.render()
        if self.inline:
            self.query_one("#live", Static).update(text)
        elif self._tool_placeholder is not None:
            self._tool_placeholder.set_running(text)

    def _after_confirm(self) -> None:
        if (running := self._running_tool) is not None:
            running.started = time.monotonic()  # time running, not awaiting you
            self._set_state(f"running {running.tool}")
            self._tick_running_tool()
        else:
            self._set_state("executing tools")

    def _stop_running_tool(self) -> None:
        self._running_tool = None
        if self._running_tool_timer is not None:
            self._running_tool_timer.stop()
            self._running_tool_timer = None

    def _generation_body(self) -> None:
        """Runs the step loop until no more runnable tools."""
        manager = self.manager
        max_steps: int | None = None
        if max_steps_str := os.environ.get("GPTME_MAX_STEPS"):
            with contextlib.suppress(ValueError):
                max_steps = int(max_steps_str)
        step_count = 0
        invocation_id = self._active_skill_invocation_id
        outcome: SkillPhase = "abandoned"
        error_type: str | None = None
        try:
            # Same boundary as the CLI chat loop: TURN_PRE after the user
            # prompt is in the log, before the first generation step.
            if turn_msgs := trigger_hook(HookType.TURN_PRE, manager=manager):
                for msg in turn_msgs:
                    manager.append(msg)
            while True:
                self.call_from_thread(self._begin_stream)
                interrupted = False
                declined = False
                response: Message | None = None
                # Ctrl+C raises KeyboardInterrupt into this thread while in
                # step() (see action_interrupt): Textual's raw-mode Ctrl+C is a
                # key, not SIGINT, so a running tool would never see it.
                self._step_processes = start_tracking()
                try:
                    with self._step_lock:
                        self._in_step = True
                    with recovery_notifications(
                        lambda notice: self.call_from_thread(self._on_retry, notice)
                    ):
                        for msg in step(
                            manager.log,
                            stream=True,
                            tool_format=self.tool_format,
                            workspace=self.workspace,
                            logdir=manager.logdir,
                            on_token=self._on_token,
                            on_thinking=self._on_thinking,
                            on_before_tool_execution=lambda m: self.call_from_thread(
                                self._on_before_tool_execution, m
                            ),
                        ):
                            # each yield may follow a tool execution that reset
                            # the tty (e.g. ipython init) — reassert raw mode
                            # immediately, not just at end of step, so later
                            # confirmations in the same step get a sane terminal
                            self._restore_terminal()
                            manager.append(msg)
                            if msg.role == "assistant":
                                response = msg
                            if msg.content == DECLINED_CONTENT:
                                declined = True
                            self.call_from_thread(self._on_step_message, msg)
                except KeyboardInterrupt:
                    interrupted = True
                finally:
                    self._leave_step()
                    # tools (e.g. ipython) may have reset the tty out of raw mode
                    self._restore_terminal()
                if interrupted:  # recorded outside the window Ctrl+C can reach
                    msg = Message("system", INTERRUPT_CONTENT)
                    manager.append(msg)
                    self.call_from_thread(self._on_step_message, msg)

                if interrupted or declined or self._interrupt_event.is_set():
                    break
                step_count += 1
                if max_steps is not None and step_count >= max_steps:
                    self.call_from_thread(
                        self._show_info,
                        f"Reached max steps limit ({max_steps}), stopping.",
                    )
                    # Mirror the natural-exit check: a step-boundary exit on a
                    # nonempty final response (no pending runnable tools) is a
                    # completion, not an abandonment.
                    if (
                        response is not None
                        and response.content.strip()
                        and not any(
                            t.is_runnable
                            for t in ToolUse.iter_from_content(response.content)
                        )
                    ):
                        outcome = "completed"
                    break
                # continue stepping while the last assistant msg has runnable tools
                if response is None:
                    break
                if not any(
                    t.is_runnable for t in ToolUse.iter_from_content(response.content)
                ):
                    if response.content.strip():
                        outcome = "completed"
                    break
                # Steering: prompts queued meanwhile join the turn now, so the
                # next step sees them instead of waiting for the turn to end.
                for msg in self.call_from_thread(self._take_steer_prompts):
                    manager.append(msg)
        except SessionCompleteException:
            # complete tool is filtered out in interactive TUI mode, but a
            # resumed autonomous conversation may still have it loaded
            outcome = "completed"
            self.call_from_thread(self._show_info, "Session marked complete.")
        except Exception as e:
            outcome = "failed"
            error_type = type(e).__name__
            logger.exception("Error in generation worker")
            self.call_from_thread(self._show_info, f"Error: {e}", True)
        finally:
            if self._interrupt_event.is_set() or self._quitting:
                outcome = "abandoned"
            record_skill_phase(
                manager.logdir, invocation_id, outcome, error_type=error_type
            )
        # NOTE: completion handling (queue dispatch etc.) happens in
        # on_worker_state_changed, which fires only after this thread has
        # fully exited self._chat_ctx — dispatching from here would make the
        # next worker re-enter a still-entered Context and crash.

    def _on_retry(self, notice: RetryNotice) -> None:
        """Discard only the failed attempt's preview before streaming restarts.

        Runs synchronously on the UI thread: no failed buffered tokens may be
        flushed into the next attempt. Committed messages, tools, and queued
        steering remain untouched.
        """
        self._clear_stream_view()
        with self._stream_lock:
            self._stream_flush_scheduled = False
        detail = (
            "Failed preview discarded; restarting response. "
            if self.inline and notice.partial_output
            else ""
        )
        self._show_info(
            f"{detail}Retrying after failed attempt "
            f"{notice.attempt}/{notice.max_attempts} "
            f"in {notice.delay:g}s: {type(notice.error).__name__}"
        )
        self._begin_stream()

    def _on_token(self, token: str) -> None:
        # called from the worker thread, per streamed line/chunk: buffer it and
        # post (without blocking the worker on the UI) at most one flush
        if self._interrupt_event.is_set():
            raise KeyboardInterrupt
        with self._stream_lock:
            self._stream_pending.append(token)
            if self._stream_flush_scheduled:
                return
            self._stream_flush_scheduled = True
        self.post_message(self.StreamPending())

    class StreamPending(TextualMessage):
        """Streamed chunks are waiting in the buffer (posted from the worker)."""

    def on_gptme_app_stream_pending(self, _: StreamPending) -> None:
        delay = self._stream_last_flush + 1 / STREAM_FPS - time.monotonic()
        if delay > 0:
            self.set_timer(delay, self._flush_stream)
        else:
            self._flush_stream()

    def _flush_stream(self) -> None:
        with self._stream_lock:
            text = "".join(self._stream_pending)
            self._stream_pending.clear()
            self._stream_flush_scheduled = False
        self._stream_last_flush = time.monotonic()
        if text:
            self._on_stream_token(text)

    def _on_thinking(self, is_thinking: bool) -> None:
        # called from the worker thread when thinking state changes
        self.call_from_thread(self._set_stream_thinking, is_thinking)

    def _set_stream_thinking(self, is_thinking: bool) -> None:
        if self._stream_widget is not None:
            self._stream_widget.set_thinking(is_thinking)
        if self.inline and not self._stream_preview().strip():
            label = "Thinking…" if is_thinking else "Generating…"
            with contextlib.suppress(Exception):
                self.query_one("#live", Static).update(Text(label, style="dim"))

    def _begin_stream(self) -> None:
        # A new model step starts only after the previous tool batch finished.
        # The stream takes the tool status line's place: removing that first
        # would shrink the chat and make everything above it jump.
        placeholder = None if self.inline else self._tool_placeholder
        self._tool_placeholder = None
        if placeholder is None:
            self._clear_tool_placeholder()
        self._message_pre_rendered = False
        self._set_state("generating")
        if self.inline:
            self.query_one("#live", Static).update(Text("Generating…", style="dim"))
        elif self._stream_widget is None:
            self._stream_widget = StreamingMessage(self.show_thinking)
            self._place_in_turn(self._stream_widget, "step")
            self._mount_in_chat(self._stream_widget, replaces=placeholder)
        elif placeholder is not None:
            placeholder.remove()

    def _stream_preview(self) -> str:
        if self.show_thinking:
            return self._stream_text
        return _strip_thinking(self._stream_text)

    def _on_stream_token(self, token: str) -> None:
        if self.inline:
            # live preview in the inline region: show the last few lines
            self._stream_text += token
            lines = self._stream_preview().strip().splitlines()[-6:]
            self.query_one("#live", Static).update(
                Text("\n".join(lines), style="bright_black")
                if lines
                else Text(
                    "Thinking…"
                    if _THINK_OPEN_RE.search(self._stream_text)
                    else "Generating…",
                    style="dim",
                )
            )
            return
        if self._stream_widget is None:
            self._stream_widget = StreamingMessage(self.show_thinking)
            self._place_in_turn(self._stream_widget, "step")
            self._mount_in_chat(self._stream_widget)
        self._stream_widget.append_token(token)

    def _clear_stream_view(self) -> None:
        self._stream_text = ""
        with self._stream_lock:
            # a pending flush belongs to the stream being replaced
            self._stream_pending.clear()
        if self.inline:
            self.query_one("#live", Static).update("")
        if self._stream_widget is not None:
            self._stream_widget.remove()
            self._stream_widget = None

    def _show_tool_placeholder(self) -> None:
        if self.inline:
            self.query_one("#live", Static).update(Text("Running tool…", style="dim"))
            return
        if self._tool_placeholder is not None:
            return
        self._tool_placeholder = ToolPlaceholder()
        self._place_in_turn(self._tool_placeholder, "result")
        self._mount_in_chat(self._tool_placeholder)

    def _clear_tool_placeholder(self) -> None:
        if self.inline:
            self.query_one("#live", Static).update("")
            return
        if self._tool_placeholder is not None:
            self._tool_placeholder.remove()
            self._tool_placeholder = None

    def _finalize_stream_in_place(self, msg: Message) -> None:
        """Mount the final AssistantMessage before the streaming widget and remove it.

        Doing both inside batch_update() avoids an intermediate frame with a
        gap: the chat scroll never sees the stream widget disappear before its
        replacement is present.
        """
        assert self._stream_widget is not None
        stream_widget = self._stream_widget
        self._stream_text = ""
        with self._stream_lock:
            # a pending flush belongs to the stream being replaced
            self._stream_pending.clear()
        self._stream_widget = None

        if msg.hide and not self.show_hidden:
            stream_widget.remove()
            return

        am = AssistantMessage(
            msg.content, show_thinking=self.show_thinking, highlight=self.highlight
        )
        self._take_turn_place(am, stream_widget)
        chat = self.query_one("#chat", VerticalScroll)
        stick = chat.scroll_offset.y >= chat.max_scroll_y - 2
        self._mount_in_chat(am, replaces=stream_widget)
        if stick:
            self.call_after_refresh(chat.scroll_end, animate=False)
        cost_text = inline_cost_text(msg)
        if cost_text:
            self._mount_in_chat(CostMessage(cost_text))

    def _on_before_tool_execution(self, msg: Message) -> None:
        """Called before tool execution: show the completed message, then the tool indicator.

        Replaces the streaming preview with the final reply so the user can
        read it while tools run. Sets _message_pre_rendered so
        _on_step_message skips the re-render when it fires after tools complete.
        """
        if not any(
            tool_use.is_runnable
            for tool_use in ToolUse.iter_from_content(
                msg.content, tool_format_override=self.tool_format
            )
        ):
            return
        if not self.inline and self._stream_widget is not None:
            # Swap stream preview for the final rendered message in one DOM update.
            self._finalize_stream_in_place(msg)
        else:
            # Clear pending tokens before printing the inline reply or rendering
            # a response that completed before its stream widget was mounted.
            self._clear_stream_view()
            self._show_message(msg)
        self._message_pre_rendered = True
        self._set_state("executing tools")
        self._show_tool_placeholder()
        self._update_status()

    def _on_step_message(self, msg: Message) -> None:
        if msg.role == "assistant" and self._running_tool is not None:
            # step() yields the assistant message once all its tools finished;
            # the status line stays until the next step's stream replaces it
            # (or the turn ends), so the chat doesn't shrink in between
            if self.inline:
                self._clear_tool_placeholder()  # the live line, not the chat
            elif self._tool_placeholder is not None:
                self._tool_placeholder.set_finished(
                    self._running_tool.render_finished()
                )
            self._stop_running_tool()
            self._set_state("generating")
        if msg.role == "assistant" and self._provisional_assistant is not None:
            self._replace_provisional_assistant(msg)
        elif msg.role == "assistant":
            if not self.inline and self._stream_widget is not None:
                # Swap streaming preview for final rendering without a DOM gap.
                self._finalize_stream_in_place(msg)
            elif self._message_pre_rendered:
                # _on_before_tool_execution already rendered the reply (including
                # inline scrollback) — skip re-render to avoid a duplicate.
                self._message_pre_rendered = False
            else:
                # Inline path or no stream widget: clear preview and show normally.
                self._clear_stream_view()
                self._show_message(msg)
        else:
            self._show_message(msg)
        self._update_status()

    def _replace_provisional_assistant(self, msg: Message) -> None:
        """Swap the message rendered when its tools started for the final one."""
        provisional = self._provisional_assistant
        self._provisional_assistant = None
        assert provisional is not None
        if not provisional.is_attached:
            self._show_message(msg)
            return
        # remove the stream widget a final message may still have
        self._clear_stream_view()
        if msg.hide and not self.show_hidden:
            provisional.remove()
            return
        if not isinstance(provisional, AssistantMessage):
            return
        if provisional.content == msg.content.strip():
            return  # already shown as it is, with its tools' results
        final = AssistantMessage(
            msg.content, show_thinking=self.show_thinking, highlight=self.highlight
        )
        if not self._carry_results(provisional, final):
            return  # the calls differ: keep the results under their calls
        self._take_turn_place(final, provisional)
        self._mount_in_chat(final, replaces=provisional)

    def _carry_results(self, old: "AssistantMessage", new: "AssistantMessage") -> bool:
        """Show results placed under ``old``'s tool calls under ``new``'s."""
        if len(old.result_slots) != len(new.result_slots):
            return False
        for call, result in old.results:
            if widget := self._widget_for(result):
                widget.add_class("turn-result")
                new.result_slots[call].pending.append(widget)
                new.results.append((call, result))
        return True

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "generation":
            return
        if event.state in (
            WorkerState.SUCCESS,
            WorkerState.ERROR,
            WorkerState.CANCELLED,
        ):
            await self._generation_done()

    async def _generation_done(self) -> None:
        # Cancellation can prevent the worker body from running at all. Successful
        # and failed turns already have a terminal event; this is then a no-op.
        record_skill_phase(
            self.manager.logdir, self._active_skill_invocation_id, "abandoned"
        )
        self._active_skill_invocation_id = None
        self.generating = False
        # stream may have ended without a final message (interrupt mid-stream)
        self._clear_stream_view()
        running = self._running_tool
        self._stop_running_tool()
        self._provisional_assistant = None
        self._settle_tool_placeholder(running)
        if self._rebuild_pending:
            self._rebuild_chat()  # a display change made during the turn
        await self._dispatch_queue()

    def _settle_tool_placeholder(self, running: _RunningTool | None) -> None:
        """At turn end, keep a tool's status line as a record of what ran
        rather than remove it: removing would shrink the chat (a jump).
        One for a tool that never started (e.g. declined) is removed."""
        placeholder = None if self.inline else self._tool_placeholder
        if placeholder is None:
            self._clear_tool_placeholder()
            return
        self._tool_placeholder = None
        if running is not None:  # interrupted while running
            placeholder.set_finished(running.render_finished("Stopped"))
        elif not placeholder.finished:
            placeholder.remove()

    async def _dispatch_queue(self) -> None:
        """After a turn or command: send the next queued prompt, or hand the
        queue back to the input if the user interrupted."""
        if self._interrupt_event.is_set() and self.prompt_queue:
            # user interrupted: hand queued text back instead of auto-submitting
            text = "\n".join(_queued_prompt_text(item) for item in self.prompt_queue)
            for item in self.prompt_queue:
                if isinstance(item, Message) and item.metadata:
                    record_skill_phase(
                        self.manager.logdir,
                        item.metadata.get("skill_invocation_id"),
                        "abandoned",
                    )
            self.prompt_queue.clear()
            self._confirmed_urls.clear()
            self._drop_delivered_widget()
            for w in self._queued_widgets:
                w.remove()
            self._queued_widgets.clear()
            self.query_one("#input", ChatInput)._set_text(text)
        elif self.prompt_queue:
            # everything you queued goes in one turn, not one per turn
            if steer := self._take_steer_prompts():
                for msg in steer:
                    self.manager.append(msg)
                self._start_generation()
                return
            prompt = self._pop_queued_prompt()
            self._set_state("idle")
            await self._submit_user_input(prompt)
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
        self.call_from_thread(self._after_confirm)
        return result["r"]

    # ------------------------------------------------------------ actions

    def _dismiss_dialog(self) -> bool:
        """Cancel an open confirmation dialog; True if there was one.

        The generation thread blocks on the tool-confirm dialog, so an
        interrupt has to answer it (as declined) to take effect.
        """
        screen: object = self.screen
        if isinstance(screen, ConfirmScreen):
            screen.dismiss(ConfirmationResult.skip(INTERRUPT_CONTENT))
        elif isinstance(screen, UrlConfirmScreen):
            screen.dismiss([])
        else:
            return False
        return True

    def action_interrupt(self) -> None:
        self._dismiss_dialog()
        if self.generating:
            self._interrupt_event.set()
            self._set_state("interrupting…")
            self._interrupt_step()

    def _leave_step(self) -> None:
        """End the window in which Ctrl+C raises into this (worker) thread.

        A raise can land while we wait for the lock or before the pending one
        is cleared; retry until the flag is down and nothing is pending.
        """
        while True:
            try:
                with self._step_lock:
                    self._in_step = False
                    clear_pending(threading.get_ident())
                break
            except KeyboardInterrupt:
                continue
        stop_tracking(self._step_processes)

    def _interrupt_step(self) -> None:
        """Stop a running tool: raise KeyboardInterrupt in the worker (the
        shell tool then kills its command, ipython stops the cell) and kill
        processes started during the step (unblocking e.g. subprocess.run)."""
        with self._step_lock:
            if not self._in_step or self._generation_thread is None:
                return
            raise_in_thread(self._generation_thread, KeyboardInterrupt)
        threading.Thread(
            target=kill_processes, args=(set(self._step_processes),), daemon=True
        ).start()

    def action_interrupt_or_quit(self) -> None:
        if self.generating:
            self.action_interrupt()
        elif not self._dismiss_dialog():  # e.g. the URL dialog before a turn
            self._request_exit()

    def action_toggle_details(self) -> None:
        """Ctrl+O: expand outputs and thinking, or collapse both if expanded."""
        on = not (self._outputs_expanded and self.show_thinking)
        self._set_outputs(on)
        self._set_thinking(on)
        if self.inline:
            # scrollback is immutable; the toggle affects future output
            state = "expanded" if on else "collapsed"
            self._show_info(f"Tool output and thinking {state} from now on.")

    def _set_outputs(self, expanded: bool) -> None:
        self._outputs_expanded = expanded
        if self.inline:
            return
        # thinking has its own setting (/display thinking)
        for collapsible in self.query(Collapsible):
            if not collapsible.has_class("thinking-block"):
                collapsible.collapsed = not expanded

    # ---------------------------------------------------- terminal focus

    async def _on_app_focus(self, event: events.AppFocus) -> None:
        event.prevent_default()  # skip App's refresh_bindings(): no footer
        self.app_focus = True

    async def _on_app_blur(self, event: events.AppBlur) -> None:
        event.prevent_default()
        self.app_focus = False

    def _watch_app_focus(self, focus: bool) -> None:
        """Replace Textual's handling of terminal focus changes (FocusIn/Out).

        Textual restyles every widget on the screen and drops keyboard focus
        on blur, restoring it on the next focus-in. With a long conversation
        that is thousands of widgets, freezing the UI for seconds on every
        tmux pane switch, and focus stays lost if the focus-in is missed or
        a dialog opened meanwhile. The TUI's CSS has no app :focus/:blur
        rules, and the input should simply keep focus.
        """
        chat_input = self.query_one("#input", ChatInput)
        if focus and self.screen.focused is None and not self._is_modal_open():
            self.screen.set_focus(chat_input, scroll_visible=False)
        chat_input.redraw_cursor()

    def _is_modal_open(self) -> bool:
        return isinstance(self.screen, ModalScreen)

    async def action_quit(self) -> None:
        self._request_exit()
