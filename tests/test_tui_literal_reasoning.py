"""Regression coverage for literal reasoning tags in TUI tool payloads (#4649)."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("textual")

from rich.panel import Panel
from rich.syntax import Syntax
from textual.widgets import Collapsible, Static

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools import ToolUse, init_tools
from gptme.tui.app import (
    AssistantMessage,
    GptmeApp,
    SystemMessage,
    _split_all_tool_calls,
    _split_thinking,
    _streaming_all_segments,
    _strip_thinking,
    renderables_for_message,
)

LITERALS = [
    "<think>literal payload</think>",
    '<thinking mode="example">literal payload</thinking>',
    "<think>unfinished literal payload",
    "<thinking redacted>unfinished literal payload",
]


def _native_call(tool: str, literal: str) -> tuple[str, dict[str, str]]:
    if tool == "ipython":
        kwargs = {"code": f"print({literal!r})"}
    elif tool == "shell":
        kwargs = {"command": f"printf '%s\\n' '{literal}'"}
    else:
        kwargs = {"path": "fixture.txt", "content": literal}
    return f"@{tool}(literal_call): {json.dumps(kwargs)}", kwargs


@pytest.mark.parametrize("tag", ["think", "thinking", 'thinking mode="private"'])
def test_unclosed_genuine_reasoning_remains_hidden(tag: str) -> None:
    content = f"Public answer.\n<{tag}>private unfinished reasoning"
    assert _split_thinking(content) == [
        (False, "Public answer.\n"),
        (True, "private unfinished reasoning"),
    ]
    assert _strip_thinking(content) == "Public answer.\n"
    assert _streaming_all_segments(content) == [
        ("prose", "Public answer.\n"),
        ("thinking", "private unfinished reasoning"),
    ]


@pytest.mark.parametrize("tool", ["ipython", "shell", "save"])
@pytest.mark.parametrize("literal", LITERALS)
def test_native_argument_reasoning_tags_are_literal(tool: str, literal: str) -> None:
    call, _ = _native_call(tool, literal)
    assert _split_thinking(call) == [(False, call)]
    assert _strip_thinking(call) == call
    assert _split_all_tool_calls(call) == [(True, "tool", call)]
    assert _streaming_all_segments(call) == [("tool_call", call)]


@pytest.mark.parametrize("tool", ["ipython", "shell", "save"])
@pytest.mark.parametrize("literal", LITERALS)
def test_native_literal_between_genuine_reasoning(tool: str, literal: str) -> None:
    call, _ = _native_call(tool, literal)
    content = (
        "<think>private before</think>\n"
        f"{call}\n<thinking>private after</thinking>\nPublic answer."
    )
    segments = _split_thinking(content)
    assert [text for thinking, text in segments if thinking] == [
        "private before",
        "private after",
    ]
    assert any(call in text for thinking, text in segments if not thinking)
    visible = _strip_thinking(content)
    assert call in visible
    assert "private before" not in visible and "private after" not in visible
    streamed = _streaming_all_segments(content)
    assert [text for kind, text in streamed if kind == "tool_call"] == [call]
    assert [text for kind, text in streamed if kind == "thinking"] == [
        "private before",
        "private after",
    ]


@pytest.mark.parametrize("tool", ["ipython", "shell", "save"])
def test_every_native_json_prefix_keeps_literal_tag_in_call(tool: str) -> None:
    """Token boundaries inside JSON/attributes/open or closing tags cannot hide code."""
    call, _ = _native_call(tool, '<thinking mode="example">literal</thinking>')
    start = call.index("{") + 1
    for end in range(start, len(call) + 1):
        prefix = call[:end]
        assert _strip_thinking(prefix) == prefix, repr(prefix)
        assert _split_thinking(prefix) == [(False, prefix)], repr(prefix)
        assert _streaming_all_segments(prefix) == [("tool_call", prefix)], repr(prefix)


@pytest.mark.parametrize("literal", LITERALS)
@pytest.mark.parametrize("fmt", ["markdown", "xml", "ordinary-fence"])
def test_fenced_and_xml_payloads_are_not_reasoning(fmt: str, literal: str) -> None:
    """Even non-tool code fences are literal examples, not private reasoning."""
    init_tools(["ipython", "shell", "save"])
    code = f"print({literal!r})"
    if fmt == "xml":
        payload = f"<tool-use>\n<ipython>\n{code}\n</ipython>\n</tool-use>"
    else:
        language = "ipython" if fmt == "markdown" else "text"
        payload = f"```{language}\n{code}\n```"
    assert _split_thinking(payload) == [(False, payload)]
    assert _strip_thinking(payload) == payload
    content = f"<think>private before</think>\n{payload}\n<think>private after</think>"
    assert [text for thinking, text in _split_thinking(content) if thinking] == [
        "private before",
        "private after",
    ]
    assert payload in _strip_thinking(content)


@pytest.mark.parametrize("show_thinking", [False, True])
def test_inline_native_literal_renders_intact_tool_panel(show_thinking: bool) -> None:
    literal = '<thinking mode="example">literal payload</thinking>\n<think>unclosed'
    call, kwargs = _native_call("save", literal)
    msg = Message("assistant", f"<think>private before</think>\n{call}")
    before = msg.content
    uses = list(ToolUse.iter_from_content(msg.content, tool_format_override="tool"))
    before_kwargs = deepcopy(uses[0].kwargs)
    panels = [
        item
        for item in renderables_for_message(msg, show_thinking=show_thinking)
        if isinstance(item, Panel)
    ]
    tool_panels = [panel for panel in panels if isinstance(panel.renderable, Syntax)]
    assert len(tool_panels) == 1
    syntax = tool_panels[0].renderable
    assert isinstance(syntax, Syntax)
    assert syntax.code == literal
    assert "fixture.txt" in str(tool_panels[0].title)
    assert len([panel for panel in panels if panel.title == "Thinking"]) == int(
        show_thinking
    )
    assert msg.content == before
    assert uses[0].kwargs == before_kwargs == kwargs
    assert (
        list(ToolUse.iter_from_content(msg.content, tool_format_override="tool"))[
            0
        ].kwargs
        == kwargs
    )


@pytest.mark.asyncio
async def test_finalized_save_literal_keeps_result_slot_and_arguments(
    tmp_path: Path,
) -> None:
    literal = '<think>literal payload</think>\n<thinking mode="example">unclosed'
    call, kwargs = _native_call("save", literal)
    msg = Message("assistant", f"<think>private before</think>\n{call}\nAll done.")
    before = msg.content
    result = Message(
        "system",
        "Saved to fixture.txt",
        call_id="literal_call",
        metadata={"tool_call": 0},
    )
    manager = LogManager(
        [Message("user", "save an example"), msg, result],
        logdir=tmp_path / "literal-conversation",
        lock=False,
    )
    app = GptmeApp(manager, workspace=tmp_path, tool_format="tool")
    async with app.run_test(size=(100, 35)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        step = app.query_one(AssistantMessage)
        assert len(step.query(".thinking-block")) == 1
        assert len(step.query(".tool-call-block")) == 1
        assert len(step.result_slots) == 1
        assert step.call_slot(0, "literal_call", "tool") is step.result_slots[0]
        rendered_result = step.query_one(SystemMessage)
        assert rendered_result.parent is step.result_slots[0]
        assert rendered_result.content == result.content
        rendered_code = [
            widget.content
            for widget in step.query_one(".tool-call-block", Collapsible).query(Static)
            if isinstance(widget.content, Syntax)
        ]
        assert len(rendered_code) == 1
        syntax = rendered_code[0]
        assert isinstance(syntax, Syntax)
        assert syntax.code == literal
        for shown in (True, False):
            app._set_thinking(shown)
            await pilot.pause()
            assert step.query_one(".thinking-block", Collapsible).collapsed is not shown
            assert len(step.result_slots) == 1
        assert step.content == msg.content == before
        assert (
            list(ToolUse.iter_from_content(msg.content, tool_format_override="tool"))[
                0
            ].kwargs
            == kwargs
        )


@pytest.mark.asyncio
async def test_streaming_toggle_does_not_invent_literal_thinking(
    tmp_path: Path,
) -> None:
    call, _ = _native_call("save", "<think>literal</think>\n<thinking>unclosed")
    manager = LogManager([], logdir=tmp_path / "stream-conversation", lock=False)
    app = GptmeApp(manager, workspace=tmp_path, tool_format="tool")
    async with app.run_test() as pilot:
        app._begin_stream()
        content = f"<think>private before</think>\n{call}"
        app._on_stream_token(content)
        app._flush_stream()
        await app.wait_rendered()
        for shown in (False, True, False):
            app._set_thinking(shown)
            await pilot.pause()
            stream = app._stream_widget
            assert stream is not None
            assert stream._buffer == content
            assert call in stream._visible()
            assert ("private before" in stream._visible()) is shown
            assert not stream.has_class("has-text")
            if not shown:
                assert len([row for row in stream._segments if row.display]) == 2
                assert (
                    len(
                        [
                            row
                            for row in stream._segments
                            if row.display and row.has_class("thinking-stub")
                        ]
                    )
                    == 1
                )
                assert (
                    len(
                        [
                            row
                            for row in stream._segments
                            if row.display and row.has_class("tool-stub")
                        ]
                    )
                    == 1
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("indexed_results", [False, True])
@pytest.mark.parametrize("pretty_json", [False, True])
async def test_multiple_native_literal_calls_keep_each_result_slot(
    tmp_path: Path, indexed_results: bool, pretty_json: bool
) -> None:
    """Literal tags must not split one call or move its result under another."""
    init_tools(["ipython", "shell", "save"])
    calls: list[str] = []
    arguments: list[dict[str, str]] = []
    results: list[Message] = []
    payloads = [
        ("ipython", "<think>literal python</think>"),
        ("save", '<thinking mode="example">unfinished saved text'),
        ("shell", "<think>unfinished shell text"),
    ]
    for index, (tool, literal) in enumerate(payloads):
        call, kwargs = _native_call(tool, literal)
        call_id = f"literal_{index}"
        if pretty_json:
            call = f"@{tool}({call_id}): {json.dumps(kwargs, indent=2)}"
        else:
            call = call.replace("(literal_call)", f"({call_id})", 1)
        calls.append(call)
        arguments.append(kwargs)
        results.append(
            Message(
                "system",
                f"Result {index}: {tool} completed",
                call_id=call_id,
                metadata={"tool_call": index} if indexed_results else None,
            )
        )
    content = (
        "<think>private before</think>\n"
        + "\n".join(calls)
        + "\n<thinking>private after</thinking>\nAll done."
    )
    msg = Message("assistant", content)
    manager = LogManager(
        [Message("user", "run the examples"), msg, *results],
        logdir=tmp_path / "multiple-literal-conversation",
        lock=False,
    )
    app = GptmeApp(manager, workspace=tmp_path, tool_format="tool")
    async with app.run_test(size=(100, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        step = app.query_one(AssistantMessage)
        assert len(step.query(".thinking-block")) == 2
        tool_blocks = list(step.query(".tool-call-block").results(Collapsible))
        assert len(tool_blocks) == len(step.result_slots) == len(payloads)
        for index, (block, slot, result) in enumerate(
            zip(tool_blocks, step.result_slots, results, strict=True)
        ):
            assert step.call_slot(None, result.call_id, "tool") is slot
            assert step.call_slot(index, result.call_id, "tool") is slot
            rendered_result = slot.query_one(SystemMessage)
            assert rendered_result.content == result.content
            assert rendered_result.parent is slot
            assert block.region.bottom == rendered_result.region.y
            code = [
                widget.content
                for widget in block.query(Static)
                if isinstance(widget.content, Syntax)
            ]
            assert len(code) == 1
            syntax = code[0]
            assert isinstance(syntax, Syntax)
            kwargs = arguments[index]
            assert syntax.code == (
                kwargs.get("code") or kwargs.get("command") or kwargs["content"]
            )
        assert step.content == msg.content == content
        uses = list(ToolUse.iter_from_content(msg.content, tool_format_override="tool"))
        assert [use.kwargs for use in uses] == arguments
        assert [use.call_id for use in uses] == [result.call_id for result in results]


@pytest.mark.parametrize("tool", ["ipython", "shell", "save"])
def test_pretty_native_json_stream_prefixes_stay_one_call(tool: str) -> None:
    """A newline inside an incomplete JSON object does not end its tool payload."""
    _, kwargs = _native_call(
        tool, '<thinking mode="example">literal</thinking>\n<think>open'
    )
    call = f"@{tool}(pretty_call): {json.dumps(kwargs, indent=2)}"
    for end in range(call.index("{") + 1, len(call) + 1):
        prefix = call[:end]
        assert _split_thinking(prefix) == [(False, prefix)], repr(prefix)
        assert _strip_thinking(prefix) == prefix, repr(prefix)
        assert _streaming_all_segments(prefix) == [("tool_call", prefix)], repr(prefix)
    content = (
        f"<think>private before</think>\n{call}\n<think>private after</think>\nDone."
    )
    assert _streaming_all_segments(content) == [
        ("thinking", "private before"),
        ("tool_call", call),
        ("thinking", "private after"),
        ("prose", "\nDone."),
    ]


@pytest.mark.parametrize(
    "xml_tag", ["tool-use", "TOOL-USE", "function_calls", "FUNCTION_CALLS"]
)
def test_xml_container_boundaries_protect_literal_reasoning(xml_tag: str) -> None:
    payload = (
        f"<{xml_tag}>\n<ipython>\n"
        "print('<think>literal</think>')\n"
        "print('<thinking mode=\"example\">unclosed')\n"
        f"</ipython>\n</{xml_tag}>"
    )
    content = f"<think>private before</think>\n{payload}\n<think>private after</think>"
    assert _split_thinking(payload) == [(False, payload)]
    assert _strip_thinking(payload) == payload
    assert [text for thinking, text in _split_thinking(content) if thinking] == [
        "private before",
        "private after",
    ]
    assert payload in _strip_thinking(content)
    # An unfinished XML payload owns its remaining stream, too.
    prefix = payload[: payload.index("</ipython>")]
    assert _split_thinking(prefix) == [(False, prefix)]
    assert _strip_thinking(prefix) == prefix


@pytest.mark.parametrize("fence", ["~~~", "~~~~", "````", "`````"])
def test_long_and_tilde_fences_keep_nested_short_fences_literal(fence: str) -> None:
    payload = (
        f"{fence}text\n"
        "```python\nprint('<think>literal</think>')\n```\n"
        '<thinking mode="example">unclosed literal\n'
        f"{fence}"
    )
    content = f"<think>private before</think>\n{payload}\n<think>private after</think>"
    assert _split_thinking(payload) == [(False, payload)]
    assert _strip_thinking(payload) == payload
    assert [text for thinking, text in _split_thinking(content) if thinking] == [
        "private before",
        "private after",
    ]
    assert payload in _strip_thinking(content)
    prefix = payload[: -len(fence)]
    assert _split_thinking(prefix) == [(False, prefix)]
    assert _strip_thinking(prefix) == prefix


def test_native_save_writes_literal_payload_bytes_after_display(tmp_path: Path) -> None:
    """Display parsing must not rewrite the arguments eventually sent to save."""
    from gptme.tools.save import execute_save

    path = tmp_path / "literal.txt"
    literal = (
        '<think>paired literal</think>\n<thinking mode="example">attributed</thinking>\n'
        'Unicode: λ 🧠; braces: {}; quotes: "; backslash: \\\n'
        "<thinking>unclosed literal\n"
    )
    kwargs = {"path": str(path), "content": literal}
    content = f"<think>private before</think>\n@save(bytes_call): {json.dumps(kwargs)}"
    msg = Message("assistant", content)
    use = list(ToolUse.iter_from_content(content, tool_format_override="tool"))[0]
    original_kwargs = deepcopy(use.kwargs)
    for show_thinking in (False, True):
        renderables_for_message(msg, show_thinking=show_thinking)
        _split_thinking(msg.content)
        _strip_thinking(msg.content)
        _streaming_all_segments(msg.content)
    assert msg.content == content
    assert use.kwargs == original_kwargs == kwargs
    results = list(execute_save(use.content, use.args, use.kwargs))
    assert results and all(result.role == "system" for result in results)
    assert path.read_bytes() == literal.encode("utf-8")
    assert use.kwargs == kwargs


@pytest.mark.asyncio
async def test_pretty_native_stream_toggle_collapses_whole_payload(
    tmp_path: Path,
) -> None:
    kwargs = {
        "path": "example.txt",
        "content": "<think>literal</think>\n<thinking>open",
    }
    call = f"@save(pretty_call): {json.dumps(kwargs, indent=2)}"
    content = f"<think>private before</think>\n{call}"
    manager = LogManager([], logdir=tmp_path / "pretty-stream", lock=False)
    app = GptmeApp(manager, workspace=tmp_path, tool_format="tool")
    async with app.run_test() as pilot:
        app._begin_stream()
        app._on_stream_token(content)
        app._flush_stream()
        await app.wait_rendered()
        for shown in (True, False, True, False):
            app._set_thinking(shown)
            await pilot.pause()
            stream = app._stream_widget
            assert stream is not None
            assert stream._buffer == content
            if shown:
                # Body mode intentionally shows the entire raw response, JSON included.
                assert stream._body.display
                assert str(stream._body.content) == content
                assert all(not row.display for row in stream._segments)
            else:
                assert not stream._body.display
                visible_rows = [row for row in stream._segments if row.display]
                assert len(visible_rows) == 2
                assert visible_rows[0].has_class("thinking-stub")
                assert visible_rows[1].has_class("tool-stub")
                assert not stream.query(".stream-prose")
                for row in visible_rows:
                    assert isinstance(row, Static)
                    assert "literal" not in str(row.content)


@pytest.mark.parametrize(
    "prefix",
    [
        "```text\r\nliteral\r\n```\r\n",
        "<tool-use>\n<shell>echo İ</shell>\n</tool-use>",
        "@shell(example): use this syntax\n",
    ],
)
def test_completed_or_noncall_boundaries_do_not_hide_later_reasoning(
    prefix: str,
) -> None:
    content = prefix + "<think>private reasoning</think>\nPublic answer."
    assert [text for thinking, text in _split_thinking(content) if thinking] == [
        "private reasoning"
    ]
    assert _strip_thinking(content) == prefix + "\nPublic answer."
    assert ("thinking", "private reasoning") in _streaming_all_segments(content)
    assert any(
        kind == "prose" and "Public answer." in text
        for kind, text in _streaming_all_segments(content)
    )
