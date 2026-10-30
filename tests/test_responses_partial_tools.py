"""Offline coverage for partial Responses streams and authoritative tool output."""

from types import SimpleNamespace
from typing import Any

import pytest

from gptme.llm.openai_responses import ResponsesStreamError, _stream_responses_events
from gptme.tools import ToolUse


def _call(number: int = 1, arguments: str = '{"command":"ls"}') -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": f"item_{number}",
        "name": "shell",
        "call_id": f"call_{number}",
        "arguments": arguments,
    }


def _event(kind: str, **values: Any) -> dict[str, Any]:
    return {"type": f"response.{kind}", **values}


def _objects(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _objects(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_objects(item) for item in value]
    return value


def _render(events: list[dict[str, Any]], sdk: bool = False) -> str:
    return "".join(_stream_responses_events(_objects(events) if sdk else events))


def _tools(content: str) -> list[ToolUse]:
    return list(ToolUse.iter_from_content(content, "tool"))


@pytest.mark.parametrize("terminal", ["done", "completed"])
@pytest.mark.parametrize("sdk", [False, True])
@pytest.mark.parametrize(
    "prefix", ["output_text.delta", "reasoning.delta", "reasoning_text.delta"]
)
def test_partial_prose_or_reasoning_recovers_terminal_tools(
    terminal: str, sdk: bool, prefix: str
) -> None:
    message = {
        "type": "message",
        "content": [{"type": "output_text", "text": "Checking."}],
    }
    output = _render(
        [
            _event(prefix, delta="Checking."),
            _event(terminal, response={"output": [message, _call()]}),
        ],
        sdk,
    )
    assert output.count("Checking.") == 1
    tools = _tools(output)
    assert len(tools) == 1
    assert tools[0].call_id == "call_1"
    assert tools[0].kwargs == {"command": "ls"}
    assert ("</think>" in output) is prefix.startswith("reasoning")


@pytest.mark.parametrize("terminal", ["done", "completed"])
@pytest.mark.parametrize("sdk", [False, True])
@pytest.mark.parametrize(
    "order",
    [
        ("function_call", "message"),
        ("message", "function_call"),
        ("function_call", "message", "function_call", "message"),
    ],
)
@pytest.mark.parametrize("partial", [None, '{"command":', '{"command":"ls"}'])
def test_terminal_recovery_preserves_mixed_output_order(
    terminal: str, sdk: bool, order: tuple[str, ...], partial: str | None
) -> None:
    events: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    expected: list[str] = []
    for index, kind in enumerate(order):
        if kind == "function_call":
            call = _call(index + 1)
            output.append(call)
            expected.append(f'\n@shell(call_{index + 1}): {{"command":"ls"}}')
            if partial is not None:
                events.extend(
                    [
                        _event(
                            "output_item.added",
                            output_index=index,
                            item={**call, "arguments": ""},
                        ),
                        _event(
                            "function_call_arguments.delta",
                            output_index=index,
                            item_id=call["id"],
                            delta=partial,
                        ),
                    ]
                )
        else:
            text = f"\nMessage {index + 1}.\n"
            output.append(
                {"type": "message", "content": [{"type": "output_text", "text": text}]}
            )
            expected.append(text)
    events.append(_event(terminal, response={"output": output}))
    rendered = _render(events, sdk)
    assert rendered == "".join(expected)
    assert len(_tools(rendered)) == order.count("function_call")


@pytest.mark.parametrize("terminal", ["done", "completed"])
@pytest.mark.parametrize("sdk", [False, True])
def test_mixed_terminal_output_does_not_replay_streamed_prose(
    terminal: str, sdk: bool
) -> None:
    first, second = _call(), _call(2, '{"command":"pwd"}')
    message = {
        "type": "message",
        "content": [{"type": "output_text", "text": "Checking.\n"}],
    }
    events = [
        _event("output_item.added", output_index=0, item={**first, "arguments": ""}),
        _event(
            "function_call_arguments.delta", output_index=0, delta=first["arguments"]
        ),
        _event("output_text.delta", delta="Checking.\n"),
        _event(terminal, response={"output": [first, message, second]}),
    ]
    assert _render(events, sdk) == (
        'Checking.\n\n@shell(call_1): {"command":"ls"}'
        '\n@shell(call_2): {"command":"pwd"}'
    )


@pytest.mark.parametrize("terminal", ["done", "completed"])
@pytest.mark.parametrize("sdk", [False, True])
def test_later_terminal_conflict_invalidates_earlier_call(
    terminal: str, sdk: bool
) -> None:
    output = _render(
        [
            _event(
                terminal,
                response={"output": [_call(), _call(arguments='{"command":"rm"}')]},
            )
        ],
        sdk,
    )
    assert not _tools(output)
    assert "@shell" not in output


@pytest.mark.parametrize("sdk", [False, True])
@pytest.mark.parametrize(
    "boundary", ["terminal", "output_item.done", "function_call_arguments.done"]
)
@pytest.mark.parametrize("partial", ["", '{"command":', '{"command":"ls"}'])
def test_snapshot_completes_or_deduplicates_arguments(
    sdk: bool, boundary: str, partial: str
) -> None:
    call = _call()
    events = [
        _event("output_item.added", output_index=0, item={**call, "arguments": ""}),
        _event(
            "function_call_arguments.delta",
            item_id="item_1",
            output_index=0,
            delta=partial,
        ),
    ]
    if boundary == "output_item.done":
        events.append(_event(boundary, output_index=0, item=call))
    elif boundary == "function_call_arguments.done":
        events.append(
            _event(
                boundary, item_id="item_1", output_index=0, arguments=call["arguments"]
            )
        )
    events.append(_event("done", response={"output": [call]}))
    assert _render(events, sdk) == '\n@shell(call_1): {"command":"ls"}'


@pytest.mark.parametrize("sdk", [False, True])
def test_interleaved_calls_and_late_metadata_use_ids_and_terminal_order(
    sdk: bool,
) -> None:
    first, second = _call(), _call(2, '{"command":"pwd"}')
    events = [
        _event(
            "function_call_arguments.delta",
            output_index=1,
            item_id="item_2",
            delta='{"command":',
        ),
        _event(
            "function_call_arguments.delta",
            output_index=0,
            item_id="item_1",
            delta='{"command":"ls"}',
        ),
        _event("output_item.added", output_index=1, item={**second, "arguments": ""}),
        _event("output_item.added", output_index=0, item={**first, "arguments": ""}),
        _event("function_call_arguments.delta", item_id="item_2", delta='"pwd"}'),
        _event("output_item.done", output_index=1, item=second),
        _event("done", response={"output": [first, second]}),
    ]
    output = _render(events, sdk)
    assert [tool.call_id for tool in _tools(output)] == ["call_1", "call_2"]
    assert [tool.kwargs for tool in _tools(output)] == [
        {"command": "ls"},
        {"command": "pwd"},
    ]


@pytest.mark.parametrize(
    "partial", ['{"command":"rm"}', '{"command":"rm', '{"command":"ls"} trailing']
)
def test_conflicting_arguments_never_emit_executable_syntax(partial: str) -> None:
    output = _render(
        [
            _event("output_item.added", output_index=0, item=_call()),
            _event("function_call_arguments.delta", output_index=0, delta=partial),
            _event("done", response={"output": [_call()]}),
        ]
    )
    assert not _tools(output)
    assert "@shell" not in output


@pytest.mark.parametrize("arguments", ['{"command":', "", "[]", "null"])
@pytest.mark.parametrize("terminal", ["done", "incomplete", "eof"])
def test_truncated_or_nonobject_arguments_never_become_tools(
    arguments: str, terminal: str
) -> None:
    events = [
        _event("output_item.added", output_index=0, item=_call()),
        _event("function_call_arguments.delta", output_index=0, delta=arguments),
    ]
    if terminal != "eof":
        events.append(
            _event(terminal, response={"output": [_call(arguments=arguments)]})
        )
    assert not _tools(_render(events))


def test_complete_call_without_terminal_output_retains_legacy_support() -> None:
    assert (
        _render(
            [
                _event("output_item.added", item=_call()),
                _event("function_call_arguments.delta", delta='{"command":"ls"}'),
                _event("done"),
            ]
        )
        == '\n@shell(call_1): {"command":"ls"}'
    )


def test_out_of_order_done_before_added_does_not_reset_arguments() -> None:
    call = _call()
    assert (
        _render(
            [
                _event(
                    "function_call_arguments.done",
                    output_index=0,
                    item_id="item_1",
                    arguments=call["arguments"],
                ),
                _event(
                    "output_item.added", output_index=0, item={**call, "arguments": ""}
                ),
                _event("done", response={"output": [call]}),
            ]
        )
        == '\n@shell(call_1): {"command":"ls"}'
    )


def test_conflicting_identity_does_not_execute_either_call() -> None:
    assert not _tools(
        _render(
            [
                _event("output_item.added", output_index=0, item=_call()),
                _event(
                    "function_call_arguments.delta",
                    output_index=0,
                    delta='{"command":"ls"}',
                ),
                _event("done", response={"output": [_call(2)]}),
            ]
        )
    )


def test_explicit_failure_after_complete_arguments_yields_no_tool() -> None:
    stream = _stream_responses_events(
        [
            _event("output_item.added", output_index=0, item=_call()),
            _event(
                "function_call_arguments.delta",
                output_index=0,
                delta='{"command":"ls"}',
            ),
            _event("failed", response={"error": {"code": "server_error"}}),
        ]
    )
    emitted: list[str] = []
    with pytest.raises(ResponsesStreamError):
        emitted.extend(stream)
    assert not _tools("".join(emitted))


def test_incomplete_item_with_valid_json_is_not_executable() -> None:
    assert not _tools(
        _render(
            [
                _event(
                    "incomplete",
                    response={"output": [{**_call(), "status": "incomplete"}]},
                ),
            ]
        )
    )
