"""Direct calls to function-only tools when ipython is not loaded (#4964)."""

from __future__ import annotations

import pytest

from gptme.message import Message
from gptme.tools import (
    clear_tools,
    execute_msg,
    get_tool,
    init_tools,
    set_tools,
    with_direct_function_calls,
)
from gptme.tools._direct_call import (
    DirectCallError,
    make_direct_call_tool,
    parse_direct_call,
)
from gptme.tools.base import ToolFunction, ToolSpec, ToolUse, set_tool_format

calls: list[tuple] = []


def fetch(url: str, max_pages: int = 1) -> str:
    """Fetch a page."""
    calls.append(("fetch", url, max_pages))
    return f"content of {url} ({max_pages})"


def announce(text: str) -> None:
    """Print some text."""
    calls.append(("announce", text))
    print(f"announced: {text}")


def explode() -> None:
    """Always fails."""
    raise RuntimeError("boom")


def as_message() -> Message:
    """Return a message."""
    return Message("system", "a message result")


FUNCTIONS = {
    tf.name: tf
    for tf in (
        ToolFunction.from_callable(f) for f in (fetch, announce, explode, as_message)
    )
}


def _fn_tool() -> ToolSpec:
    return ToolSpec(
        name="webby",
        desc="Test function-only tool",
        instructions="Use the functions.",
        functions=[fetch, announce, explode, as_message],
    )


@pytest.fixture(autouse=True)
def _reset():
    calls.clear()
    clear_tools()
    yield
    clear_tools()
    set_tool_format("markdown")


# --- parsing ----------------------------------------------------------------


def test_parse_positional_and_keyword_literals():
    tf, args, kwargs = parse_direct_call(
        'fetch("https://example.com", max_pages=3)', FUNCTIONS
    )
    assert tf.name == "fetch"
    assert args == ["https://example.com"]
    assert kwargs == {"max_pages": 3}


def test_parse_accepts_container_literals_and_surrounding_whitespace():
    _, args, _ = parse_direct_call(
        '\n  announce(["a", {"b": (1, -2.5, None)}])\n', FUNCTIONS
    )
    assert args == [["a", {"b": (1, -2.5, None)}]]


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("fetch(url)", "not a literal"),
        ('fetch(__import__("os").system("id"))', "not a literal"),
        ('fetch("a" + "b")', "not a literal"),
        ('fetch(url=os.environ["HOME"])', "not a literal"),
        ("fetch(*urls)", r"\*args"),
        ("fetch(**opts)", r"\*\*kwargs"),
        ('fetch("a"); fetch("b")', "invalid syntax"),
        ('fetch("a")\nfetch("b")', "invalid syntax"),
        ('x = fetch("a")', "invalid syntax"),
        ('os.system("id")', "Expected exactly one function call"),
        ('"just a string"', "Expected exactly one function call"),
        ('eval("1")', "Unknown function 'eval'"),
    ],
)
def test_parse_rejects_anything_but_one_literal_call(code: str, reason: str):
    with pytest.raises(DirectCallError, match=reason):
        parse_direct_call(code, FUNCTIONS)


# --- execution --------------------------------------------------------------


def _run(tool: ToolSpec, code: str | None, kwargs: dict | None = None) -> str:
    assert tool.execute is not None
    result = tool.execute(code, [] if code is not None else None, kwargs)
    assert not isinstance(result, Message)
    msgs = list(result)
    assert len(msgs) == 1
    return msgs[0].content


def test_direct_call_tool_is_runnable_and_keeps_functions():
    tool = make_direct_call_tool(_fn_tool())
    assert tool.is_runnable
    assert not tool.is_function_only
    assert tool.functions == _fn_tool().functions
    assert "webby" in tool.block_types
    assert [p.name for p in tool.parameters] == ["code"]
    assert "'ipython' tool is not loaded" in tool.instructions


def test_direct_call_returns_result_with_typed_literals():
    tool = make_direct_call_tool(_fn_tool())
    out = _run(tool, 'fetch("https://example.com", max_pages=2)')
    assert calls == [("fetch", "https://example.com", 2)]
    assert "content of https://example.com (2)" in out


def test_direct_call_accepts_code_kwarg_for_tool_format():
    tool = make_direct_call_tool(_fn_tool())
    out = _run(tool, None, {"code": 'fetch("u")'})
    assert "content of u (1)" in out


def test_direct_call_captures_printed_output():
    tool = make_direct_call_tool(_fn_tool())
    out = _run(tool, 'announce("hi")')
    assert "announced: hi" in out


def test_direct_call_reports_function_errors():
    tool = make_direct_call_tool(_fn_tool())
    out = _run(tool, "explode()")
    assert "RuntimeError: boom" in out


def test_direct_call_passes_message_results_through():
    tool = make_direct_call_tool(_fn_tool())
    assert _run(tool, "as_message()") == "a message result"


def test_direct_call_rejects_code_without_running_anything():
    tool = make_direct_call_tool(_fn_tool())
    out = _run(tool, 'fetch(__import__("os").system("echo pwned"))')
    assert calls == []
    assert "not a literal" in out
    # The error teaches the model the accepted form.
    assert "exactly one call" in out


def test_direct_call_without_code_explains_usage():
    tool = make_direct_call_tool(_fn_tool())
    out = _run(tool, None, {"agent_id": "x"})
    assert "No function call given" in out
    assert 'fetch(url="...")' in out


def test_direct_call_uses_callable_name_not_metadata_alias():
    """The prompt lists fn.__name__ (as ipython registers it); dispatch on that."""
    tool = make_direct_call_tool(
        ToolSpec(
            name="aliased",
            desc="",
            functions=[ToolFunction(name="metadata_alias", fn=fetch)],
        )
    )
    assert "content of u (1)" in _run(tool, 'fetch("u")')
    assert "Unknown function 'metadata_alias'" in _run(tool, 'metadata_alias("u")')


def test_direct_call_with_non_string_code_explains_usage():
    tool = make_direct_call_tool(_fn_tool())
    assert "No function call given" in _run(tool, None, {"code": 123})


def test_unloaded_function_only_tool_hint_does_not_suggest_ipython(monkeypatch):
    """Allowlisting the tool is enough now; never steer toward granting ipython."""
    monkeypatch.setattr("gptme.tools.python.registered_functions", {})
    set_tool_format("tool")
    init_tools(allowlist=["shell"])
    results = list(
        execute_msg(Message("assistant", '@subagent(call-x): {"code": "x()"}'))
    )
    assert len(results) == 1
    assert "not available for execution" in results[0].content
    assert "ipython" not in results[0].content


def test_make_direct_call_tool_requires_function_only_tool():
    with pytest.raises(ValueError, match="not function-only"):
        make_direct_call_tool(make_direct_call_tool(_fn_tool()))


def test_with_direct_function_calls_noop_when_ipython_present():
    ipython = ToolSpec(name="ipython", desc="", execute=lambda c, a, k: iter(()))  # type: ignore[arg-type]
    tools = [_fn_tool(), ipython]
    assert with_direct_function_calls(tools) is tools
    assert tools[0].is_function_only


def test_markdown_block_executes_through_execute_msg():
    set_tools(with_direct_function_calls([_fn_tool()]))
    msg = Message("assistant", '```webby\nfetch("https://example.com")\n```')
    results = list(execute_msg(msg))
    assert calls == [("fetch", "https://example.com", 1)]
    assert any("content of https://example.com" in m.content for m in results)


def test_tool_format_call_executes_through_execute_msg():
    set_tool_format("tool")
    set_tools(with_direct_function_calls([_fn_tool()]))
    msg = Message("assistant", '@webby(call-1): {"code": "fetch(\\"u\\", 5)"}')
    results = list(execute_msg(msg))
    assert calls == [("fetch", "u", 5)]
    assert results[-1].call_id == "call-1"
    assert "content of u (5)" in results[-1].content


def test_examples_render_direct_call():
    set_tools(with_direct_function_calls([_fn_tool()]))
    tool = get_tool("webby")
    assert tool is not None
    examples = tool.get_examples("markdown")
    assert '```webby\nfetch(url="...")\n```' in examples
    assert "ipython" not in examples
    assert ToolUse("webby", [], 'fetch(url="...")').to_output("markdown") in examples


def test_duplicate_keywords_do_not_execute():
    tool = make_direct_call_tool(_fn_tool())
    out = _run(tool, 'fetch("u", max_pages=1, max_pages=2)')
    assert calls == []
    assert "Repeated keyword argument: max_pages" in out


@pytest.mark.parametrize("many", [False, True])
def test_message_results_keep_captured_diagnostics(many):
    import sys

    message = Message("system", "done")

    def report():
        print("warning")
        print("diagnostic", file=sys.stderr)
        return [message] if many else message

    tool = make_direct_call_tool(ToolSpec(name="reporter", desc="", functions=[report]))
    assert tool.execute is not None
    result = tool.execute("report()", [], None)
    assert not isinstance(result, Message)
    msgs = list(result)
    assert msgs[-1] is message
    assert "warning" in msgs[0].content
    assert "diagnostic" in msgs[0].content


@pytest.mark.parametrize("name", ["screenshot", "screenshot_url"])
def test_image_path_results_attach_pixels(tmp_path, name):
    from PIL import Image

    image_path = tmp_path / "capture.png"
    Image.new("RGB", (2, 2)).save(image_path)

    def capture():
        return image_path

    capture.__name__ = name
    tool = make_direct_call_tool(ToolSpec(name="capture", desc="", functions=[capture]))
    assert tool.execute is not None
    result = tool.execute(f"{name}()", [], None)
    assert not isinstance(result, Message)
    msgs = list(result)
    assert any(image_path in m.files for m in msgs)


def test_invalid_image_result_reports_error(tmp_path):
    image_path = tmp_path / "broken.png"
    image_path.write_text("not an image")

    def capture():
        return image_path

    tool = make_direct_call_tool(ToolSpec(name="capture", desc="", functions=[capture]))
    assert "Error: UnidentifiedImageError" in _run(tool, "capture()")


def test_image_results_respect_vision_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gptme.tools.vision._vision_allowed_roots", lambda: [tmp_path, tmp_path]
    )

    def capture():
        return tmp_path.parent / "outside.png"

    tool = make_direct_call_tool(ToolSpec(name="capture", desc="", functions=[capture]))
    assert tool.execute is not None
    result = tool.execute("capture()", [], None)
    assert not isinstance(result, Message)
    msgs = list(result)
    assert all(not m.files for m in msgs)
    assert "outside allowed directories" in msgs[0].content


@pytest.mark.parametrize("fmt", ["markdown", "tool"])
@pytest.mark.parametrize("name", ["browser", "chats"])
def test_format_specific_guidance_uses_direct_calls(name, fmt):
    from importlib import import_module

    # Rendering guidance needs the real spec, not an installed browser backend.
    # Keep these assertions active in no-extras installs too.
    spec = import_module(f"gptme.tools.{name}").tool
    tool = make_direct_call_tool(spec)
    instructions = tool.get_instructions(fmt)
    assert "Use ipython" not in instructions
    assert "exactly one call" in instructions
    native = tool.instructions_format["tool"]
    assert "exactly one call" in native
    assert len(native) <= 1024


# --- integration with init_tools / load_tool --------------------------------


def test_init_tools_without_ipython_makes_function_only_tools_callable(monkeypatch):
    monkeypatch.setattr("gptme.tools.python.registered_functions", {})
    tools = init_tools(allowlist=["shell", "subagent"])
    assert {t.name for t in tools} == {"shell", "subagent"}
    subagent = get_tool("subagent")
    assert subagent is not None
    assert subagent.is_runnable
    assert not subagent.is_function_only


def test_init_tools_with_ipython_keeps_function_only_tools(monkeypatch):
    monkeypatch.setattr("gptme.tools.python.registered_functions", {})
    init_tools(allowlist=["shell", "subagent", "ipython"])
    subagent = get_tool("subagent")
    assert subagent is not None
    assert subagent.is_function_only


def test_load_tool_without_ipython_makes_tool_callable(monkeypatch):
    from gptme.tools import load_tool

    monkeypatch.setattr("gptme.tools.python.registered_functions", {})
    init_tools(allowlist=["shell"])
    loaded = load_tool("chats", allow_required=True)
    assert loaded.is_runnable
    chats = get_tool("chats")
    assert chats is loaded
