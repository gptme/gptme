"""Native schemas and replay must use safe names without changing executor names."""

import re
from collections.abc import Generator
from contextvars import copy_context
from typing import Any

import pytest

from gptme.llm.llm_anthropic import _handle_tools as anthropic_history
from gptme.llm.llm_anthropic import _spec2tool as anthropic_tool
from gptme.llm.llm_openai import _handle_tools as openai_history
from gptme.llm.llm_openai import _spec2tool as openai_tool
from gptme.llm.models import get_model
from gptme.llm.openai_responses import (
    _messages_to_responses_input,
    _tool_spec_to_responses_tool,
)
from gptme.message import Message
from gptme.tools import ToolSpec, ToolUse, set_tools
from gptme.tools.native_names import canonical_tool_name, native_tool_name


def execute(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Generator[Message, None, None]:
    yield Message("system", "created")


def schema(spec: ToolSpec, provider: str) -> Any:
    if provider == "openai":
        return openai_tool(spec, get_model("openai/gpt-4o"))["function"]
    if provider == "anthropic":
        return anthropic_tool(spec)
    return _tool_spec_to_responses_tool(spec)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "responses"])
def test_qualified_native_names_roundtrip(provider: str) -> None:
    specs = [
        ToolSpec(name=name, desc="test", execute=execute)
        for name in (
            "wordcraft.new_document",
            "other.new_document",
            "wordcraft_new_document",
            "hyphen-tool",
            "server." + "long_name" * 20,
        )
    ]
    set_tools(specs)
    names = [schema(spec, provider)["name"] for spec in specs]
    assert len(set(names)) == len(specs)
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) for name in names)
    assert names[2:4] == [spec.name for spec in specs[2:4]]
    for spec, name in zip(specs, names, strict=True):
        call = next(ToolUse.iter_from_content(f"@{name}(call_1): {{}}", "tool"))
        assert call.tool == spec.name
        assert call.is_runnable
        assert list(call.execute())[-1].content == "created"
        assert spec.name == call.tool


@pytest.mark.parametrize("provider", ["openai", "anthropic", "responses"])
def test_native_alias_cannot_shadow_valid_tool(provider: str) -> None:
    qualified = ToolSpec(name="wordcraft.new_document", desc="test")
    alias = schema(qualified, provider)["name"]
    collision = ToolSpec(name=alias, desc="test")
    set_tools([qualified, collision])
    names = [schema(spec, provider)["name"] for spec in (qualified, collision)]
    assert names[0] != names[1]
    assert names[1] == alias
    for spec, name in zip((qualified, collision), names, strict=True):
        call = next(ToolUse.iter_from_content(f"@{name}(call_1): {{}}", "tool"))
        assert call.tool == spec.name
    set_tools([collision, qualified])
    assert schema(qualified, provider)["name"] == names[0]


@pytest.mark.parametrize("provider", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize(
    "name",
    [
        "wordcraft.new_document",
        "docs/team.search",
        "docs team.search",
        "docs:team.search",
        "docs(team).search",
    ],
)
def test_canonical_history_replays_with_native_name(provider: str, name: str) -> None:
    spec = ToolSpec(name=name, desc="test", execute=execute)
    set_tools([spec])
    alias = schema(spec, provider)["name"]
    messages = [
        Message("assistant", f"@{name}(call_1): {{}}"),
        Message("system", "created", call_id="call_1"),
    ]
    calls = list(ToolUse.iter_from_content(messages[0].content, "tool"))
    assert len(calls) == 1
    assert calls[0].tool == name
    assert calls[0].call_id == "call_1"
    assert calls[0].is_runnable
    assert list(calls[0].execute())[-1].content == "created"
    if provider == "responses":
        _, items = _messages_to_responses_input(messages)
        call = next(item for item in items if item.get("type") == "function_call")
        assert call["name"] == alias
    else:
        history = openai_history if provider == "openai" else anthropic_history
        items = list(history(message.to_dict() for message in messages))
        if provider == "openai":
            assert items[0]["tool_calls"][0]["function"]["name"] == alias
        else:
            assert items[0]["content"][0]["name"] == alias
    assert "." not in alias


def test_aliases_are_context_local_and_unknown_names_stay_unknown() -> None:
    spec = ToolSpec(name="wordcraft.new_document", desc="test")
    set_tools([spec])
    alias = native_tool_name(spec.name)

    def child() -> None:
        set_tools([ToolSpec(name=alias, desc="child tool")])
        assert canonical_tool_name(alias) == alias

    copy_context().run(child)
    assert canonical_tool_name(alias) == spec.name
    assert canonical_tool_name("not_loaded") == "not_loaded"
    set_tools([])
    assert canonical_tool_name(alias) == alias


@pytest.mark.parametrize("tool_format", ["markdown", "xml"])
def test_non_native_formats_keep_canonical_names(tool_format: str) -> None:
    spec = ToolSpec(
        name="wordcraft.new_document",
        block_types=["wordcraft.new_document"],
        desc="test",
        execute=execute,
    )
    set_tools([spec])
    content = (
        "```wordcraft.new_document\n{}\n```"
        if tool_format == "markdown"
        else "<tool-use><wordcraft.new_document>{}</wordcraft.new_document></tool-use>"
    )
    call = next(ToolUse.iter_from_content(content, tool_format))  # type: ignore[arg-type]
    assert call.tool == spec.name


def test_builtin_block_name_is_unchanged() -> None:
    spec = ToolSpec(name="python", block_types=["ipython"], desc="test")
    set_tools([spec])
    assert native_tool_name("python") == "ipython"
    assert schema(spec, "responses")["name"] == "ipython"
    assert canonical_tool_name("ipython") == "ipython"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "responses"])
def test_block_name_is_resolved_only_once(provider: str) -> None:
    specs = [
        ToolSpec(name="a", block_types=["b"], desc="first"),
        ToolSpec(name="b", block_types=["c"], desc="second"),
    ]
    set_tools(specs)
    assert [schema(spec, provider)["name"] for spec in specs] == ["b", "c"]


@pytest.mark.parametrize(
    "name",
    [
        "wordcraft.new_document",
        "docs/team.search",
        "docs team.search",
        "docs:team.search",
    ],
)
def test_returned_call_is_canonical_before_registry_changes(name: str) -> None:
    from gptme.llm.openai_responses import _stream_responses_events

    spec = ToolSpec(name=name, desc="test")
    set_tools([spec])
    alias = native_tool_name(spec.name)
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "name": alias, "call_id": "call_1"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": "{}",
        },
    ]
    content = "".join(_stream_responses_events(events))
    assert f"@{spec.name}(call_1)" in content
    set_tools([spec, ToolSpec(name=alias, desc="new collision")])
    call = next(ToolUse.iter_from_content(content, "tool"))
    assert call.tool == spec.name
    _, items = _messages_to_responses_input(
        [Message("assistant", content), Message("system", "created", call_id="call_1")]
    )
    assert items[0]["name"] == native_tool_name(spec.name)
    assert items[0]["name"] != alias


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_native_tool_name_cannot_span_lines(newline: str) -> None:
    content = f"@not a call{newline}docs/team.search(call_1): {{}}"
    assert list(ToolUse.iter_from_content(content, "tool")) == []
