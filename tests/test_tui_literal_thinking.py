"""Reasoning-looking tool arguments are data, not TUI Thinking blocks (#4649)."""

import json
from pathlib import Path

import pytest

pytest.importorskip("textual")

from rich.panel import Panel
from rich.syntax import Syntax
from textual.widgets import Collapsible, Static

from gptme.logmanager import LogManager
from gptme.message import Message, MessageMetadata
from gptme.tools import init_tools
from gptme.tools.base import ToolUse
from gptme.tools.save import execute_save_impl
from gptme.tui.app import (
    AssistantMessage,
    ChatMarkdown,
    GptmeApp,
    StreamingMessage,
    SystemMessage,
    _split_all_tool_calls,
    _split_thinking,
    _streaming_all_segments,
    _strip_thinking,
    _tool_call_renderable,
    renderables_for_message,
)

PAYLOADS = [
    "<think>literal</think> then an unclosed <think>",
    "<thinking>literal</thinking> then an unclosed <thinking>",
    "<think redacted>literal</think redacted>",
    'quote: " and braces: { } and \\ and <think>literal</think>',
]


def native_call(tool: str, payload: str, *, indent: int | None = None) -> str:
    key = {"ipython": "code", "shell": "command", "save": "content"}[tool]
    args = {key: payload}
    if tool == "save":
        args["path"] = "/tmp/report.md"
    return f"@{tool}(literal): " + json.dumps(args, indent=indent)


@pytest.mark.parametrize("tool", ["ipython", "shell", "save"])
@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("indent", [None, 2])
def test_native_arguments_are_not_thinking(
    tool: str, payload: str, indent: int | None
) -> None:
    call = native_call(tool, payload, indent=indent)
    assert _split_thinking(call) == [(False, call)]
    assert _strip_thinking(call) == call
    assert _split_all_tool_calls(call) == [(True, "tool", call)]
    assert _tool_call_renderable(call)[1] == payload
    uses = list(ToolUse.iter_from_content(call, tool_format_override="tool"))
    assert len(uses) == 1
    assert payload in (uses[0].kwargs or {}).values()


@pytest.mark.parametrize("indent", [None, 2])
def test_every_native_stream_prefix_keeps_payload_together(indent: int | None) -> None:
    call = native_call("save", "example\n" + PAYLOADS[0], indent=indent)
    intro = "I will save this.\n"
    for end in range(call.index("{") + 1, len(call) + 1):
        partial = call[:end]
        assert _streaming_all_segments(intro + partial) == [
            ("prose", intro),
            ("tool_call", partial),
        ]
        assert _strip_thinking(partial) == partial
    assert _streaming_all_segments(intro + call + "\n<think>real</think>\nDone") == [
        ("prose", intro),
        ("tool_call", call),
        ("thinking", "real"),
        ("prose", "\nDone"),
    ]


def test_outside_reasoning_and_multiple_calls() -> None:
    first = native_call("ipython", PAYLOADS[0])
    second = native_call("shell", PAYLOADS[1]).replace("(literal)", "(second)")
    content = f"<think>before</think>\n{first}\n<thinking>between</thinking>\n{second}\n<think>after"
    split = _split_thinking(content)
    assert [s for thinking, s in split if thinking] == ["before", "between", "after"]
    calls = [
        part
        for thinking, text in split
        if not thinking
        for is_tool, _, part in _split_all_tool_calls(text)
        if is_tool
    ]
    assert calls == [first, second]
    assert _strip_thinking(content) == f"\n{first}\n\n{second}\n"


@pytest.mark.parametrize(
    "block",
    [
        "```save report.xml\n<think>literal</think>\n<thinking>\n```",
        "````text\n```\n<think>literal</think>\n```\n````",
        "~~~text\n<think>literal</think>\n~~~",
        "`<think>literal</think>`",
        '<tool-use><save path="report.xml"><think>literal</think></save></tool-use>',
        '<function_calls><invoke name="save"><think>literal</think></invoke></function_calls>',
    ],
)
def test_other_literal_regions_preserve_tags(block: str) -> None:
    assert _split_thinking(block) == [(False, block)]
    content = f"<think>before</think>\n{block}\n<thinking>after</thinking>"
    assert [s for thinking, s in _split_thinking(content) if thinking] == [
        "before",
        "after",
    ]
    assert _strip_thinking(content) == f"\n{block}\n"


@pytest.mark.parametrize(
    "block",
    [
        "```save report.xml\n<think>literal</think>\n<thinking>",
        '<tool-use><save path="report.xml"><think>literal</think>',
        '<function_calls><invoke name="save"><think>literal</think>',
    ],
)
def test_open_tool_regions_stream_as_one_stub(block: str) -> None:
    init_tools(["save"])
    assert _streaming_all_segments(block) == [("tool_call", block)]
    assert _strip_thinking(block) == block


@pytest.mark.parametrize(
    "draft",
    [
        native_call("shell", "echo draft"),
        "```shell\necho draft\n```",
    ],
)
def test_tools_inside_reasoning_stay_reasoning(draft: str) -> None:
    for ending in ("", "\n</think>"):
        text = f"<think>considering\n{draft}{ending}"
        assert _streaming_all_segments(text) == [("thinking", f"considering\n{draft}")]
        assert _strip_thinking(text) == ""


@pytest.mark.parametrize("show_thinking", [False, True])
def test_inline_rendering_preserves_tool_payload(show_thinking: bool) -> None:
    call = native_call("ipython", PAYLOADS[0])
    message = Message("assistant", "Prose before the call.\n" + call)
    items = renderables_for_message(message, show_thinking=show_thinking)
    panels = [item for item in items if isinstance(item, Panel)]
    assert len(panels) == 1
    assert isinstance(panels[0].renderable, Syntax)
    assert panels[0].renderable.code == PAYLOADS[0]
    assert message.content.endswith(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("call_index", [None, 0])
async def test_save_result_belongs_to_intact_call(
    tmp_path: Path, call_index: int | None
) -> None:
    init_tools(["save"])
    payload = "Issue example\n" + PAYLOADS[0] + '\n```python\nvalue = "<thinking>"\n```'
    path = tmp_path / "report.md"
    call = "@save(saved): " + json.dumps({"path": str(path), "content": payload})
    # Exercise the actual writer as well as the display; no model or mocked tool.
    results = list(execute_save_impl(payload, path))
    assert path.read_text() == payload + "\n"
    assert any("Saved to" in result.content for result in results)
    metadata: MessageMetadata = {} if call_index is None else {"tool_call": call_index}
    result = Message("system", f"Saved to {path}", call_id="saved", metadata=metadata)
    content = "Prose before the call.\n" + call
    manager = LogManager(
        [Message("assistant", content), result], logdir=tmp_path / "log", lock=False
    )
    app = GptmeApp(manager, workspace=tmp_path, tool_format="tool")
    async with app.run_test(size=(100, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        step = app.query_one(AssistantMessage)
        block = step.query_one(".tool-call-block", Collapsible)
        assert not step.query(".thinking-block")
        assert len(step.result_slots) == 1
        output = app.query_one(SystemMessage)
        assert output.parent is step.result_slots[0]
        assert block.region.y > step.query_one(ChatMarkdown).region.y
        assert output.region.y == block.region.bottom
        assert step.call_slot(call_index, "saved", "tool") is output.parent
        for show in (True, False):
            app._set_thinking(show)
            block.collapsed = not show
            await pilot.pause()
            body = block.query_one("Contents").query_one(Static)
            assert isinstance(body.content, Syntax)
            assert body.content.code == payload
            assert not step.query(".thinking-block")
        assert manager.log[0].content == content
        assert path.read_text() == payload + "\n"


@pytest.mark.asyncio
async def test_streaming_widget_does_not_expose_raw_arguments(tmp_path: Path) -> None:
    manager = LogManager([], logdir=tmp_path / "log", lock=False)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        widget = StreamingMessage(show_thinking=False)
        await app.mount(widget)
        call = native_call("save", "PAYLOAD_SENTINEL\n" + PAYLOADS[0], indent=2)
        for chunk in call.splitlines(keepends=True):
            widget.append_token(chunk)
        await pilot.pause()
        rows = [row for row in widget._segments if row.display]
        assert len(rows) == 1
        assert rows[0].has_class("tool-stub")
        assert not widget.query(".thinking-stub")
        assert "PAYLOAD_SENTINEL" not in str(rows[0].render())
        assert widget._buffer == call
        for show in (True, False):
            widget.show_thinking = show
            widget._refresh_thinking_display()
            await pilot.pause()
            assert not widget._body.display
            assert "PAYLOAD_SENTINEL" not in str(rows[0].render())
            assert not widget.query(".thinking-stub")
        widget.append_token("\n<think>real reasoning</think>\nDone")
        for show in (True, False):
            widget.show_thinking = show
            widget._refresh_thinking_display()
            await pilot.pause()
            reasoning = widget.query_one(".thinking-stub", Static)
            assert ("real reasoning" in str(reasoning.render())) is show
            assert not widget._body.display


@pytest.mark.asyncio
@pytest.mark.parametrize("show_thinking", [False, True])
async def test_inline_stream_folds_payload_even_with_thinking_visible(
    tmp_path: Path, show_thinking: bool
) -> None:
    manager = LogManager([], logdir=tmp_path / "log", lock=False)
    app = GptmeApp(manager, workspace=tmp_path, inline=True)
    app.show_thinking = show_thinking
    async with app.run_test() as pilot:
        app._begin_stream()
        call = native_call("save", "PAYLOAD_SENTINEL\n" + PAYLOADS[0], indent=2)
        app._on_stream_token("Introduction.\n" + call)
        await pilot.pause()
        live = str(app.query_one("#live", Static).render())
        assert "Introduction." in live and "save" in live
        assert "PAYLOAD_SENTINEL" not in live and '"content"' not in live
        assert app._stream_text.endswith(call)


@pytest.mark.parametrize(
    "wrapper",
    [
        "```text\n{}\n```",
        '<function_calls><invoke name="save">{}</invoke></function_calls>',
    ],
)
def test_native_examples_inside_other_payloads_are_not_separate_calls(
    wrapper: str,
) -> None:
    call = native_call("save", PAYLOADS[0])
    block = wrapper.format("\n" + call + "\n")
    assert _streaming_all_segments(block) == [
        ("prose" if block.startswith("```text") else "tool_call", block)
    ]
    assert not any(
        is_tool and fmt == "tool" for is_tool, fmt, _ in _split_all_tool_calls(block)
    )


def test_mcp_native_name_and_literal_json_braces() -> None:
    args = {"code": 'value = "}\\"<think>literal</think>{"', "nested": {"value": "}"}}
    call = "@mcp server/read(path)(call-1): " + json.dumps(args)
    assert _split_all_tool_calls(call) == [(True, "tool", call)]
    assert _tool_call_renderable(call)[1] == args["code"]
    assert _split_thinking(call) == [(False, call)]
