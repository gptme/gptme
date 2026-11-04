"""Tests for the form tool."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gptme.tools import init_tools
from gptme.tools.form import execute_form, parse_field, parse_form_content


def test_parse_field_text():
    """Test parsing a simple text field."""
    field = parse_field("name: What is your name?")
    assert field is not None
    assert field["name"] == "name"
    assert field["prompt"] == "What is your name?"
    assert field["type"] == "text"


def test_parse_field_select():
    """Test parsing a select field with options."""
    field = parse_field("priority: Priority level [low, medium, high]")
    assert field is not None
    assert field["name"] == "priority"
    assert field["prompt"] == "Priority level"
    assert field["type"] == "select"
    assert field["options"] == ["low", "medium", "high"]


def test_parse_field_boolean():
    """Test parsing a boolean field."""
    field = parse_field("confirm: Proceed? [yes, no]")
    assert field is not None
    assert field["name"] == "confirm"
    assert field["prompt"] == "Proceed?"
    assert field["type"] == "boolean"


def test_parse_field_number():
    """Test parsing a number field."""
    field = parse_field("count: How many items? (number)")
    assert field is not None
    assert field["name"] == "count"
    assert field["prompt"] == "How many items?"
    assert field["type"] == "number"


def test_parse_field_empty():
    """Test parsing empty or invalid lines."""
    assert parse_field("") is None
    assert parse_field("   ") is None
    assert parse_field("no colon here") is None
    assert parse_field(":") is None
    assert parse_field("field:") is None


def test_parse_form_content():
    """Test parsing a complete form definition."""
    content = """
    name: Project name?
    description: Brief description?
    language: Primary language [python, javascript, rust]
    priority: Priority [low, medium, high]
    count: Number of tasks (number)
    confirm: Ready to proceed? [yes, no]
    """
    fields = parse_form_content(content)

    assert len(fields) == 6
    assert fields[0]["name"] == "name"
    assert fields[0]["type"] == "text"
    assert fields[1]["name"] == "description"
    assert fields[1]["type"] == "text"
    assert fields[2]["name"] == "language"
    assert fields[2]["type"] == "select"
    assert fields[3]["name"] == "priority"
    assert fields[3]["type"] == "select"
    assert fields[4]["name"] == "count"
    assert fields[4]["type"] == "number"
    assert fields[5]["name"] == "confirm"
    assert fields[5]["type"] == "boolean"


def test_form_tool_registered():
    """Test that the form tool is properly registered."""
    init_tools([])
    # Form tool is disabled by default, so it won't be in the loaded tools
    # but it should be in available tools
    from gptme.tools import get_available_tools

    available = get_available_tools()
    available_names = [t.name for t in available]
    assert "form" in available_names


def test_parse_field_select_with_spaces():
    """Test parsing select field with spaces in options."""
    field = parse_field("status: Current status [in progress, on hold, completed]")
    assert field is not None
    assert field["type"] == "select"
    assert field["options"] == ["in progress", "on hold", "completed"]


def test_parse_field_boolean_case_insensitive():
    """Test that yes/no detection is case insensitive."""
    field1 = parse_field("ok: Continue? [Yes, No]")
    assert field1 is not None
    assert field1["type"] == "boolean"

    field2 = parse_field("ok: Continue? [YES, NO]")
    assert field2 is not None
    assert field2["type"] == "boolean"


@pytest.mark.parametrize(
    "options", ["yes/no", "YES/No", " yes / no ", "yes, no", "NO, YES"]
)
def test_parse_boolean_syntax(options: str) -> None:
    assert parse_field(f"confirm: Proceed? [{options}]") == {
        "name": "confirm",
        "prompt": "Proceed?",
        "type": "boolean",
    }


@pytest.mark.parametrize(
    "options",
    [
        "yes/no/maybe",
        "no/yes",
        "on/off",
        "/tmp/data",
        "https://example.test",
        "yes/no, maybe",
    ],
)
def test_slashes_in_select_labels_are_preserved(options: str) -> None:
    field = parse_field(f"item: Choose [{options}]")
    assert field is not None
    assert field["type"] == "select"
    assert field["options"] == [part.strip() for part in options.split(",")]


@pytest.mark.parametrize("input_format", ["markdown", "native"])
@pytest.mark.parametrize("options", ["yes/no", " YES / no ", "yes, no"])
@pytest.mark.parametrize("answer", [True, False])
def test_execute_boolean_json(
    monkeypatch: pytest.MonkeyPatch, input_format: str, options: str, answer: bool
) -> None:
    confirm = Mock(return_value=SimpleNamespace(ask=lambda: answer))
    select = Mock(return_value=SimpleNamespace(ask=lambda: "yes/no"))
    monkeypatch.setattr("questionary.confirm", confirm)
    monkeypatch.setattr("questionary.select", select)
    fields = f"confirm: Proceed? [{options}]"
    messages = (
        list(execute_form(fields, None, None))
        if input_format == "markdown"
        else list(execute_form(None, None, {"fields": fields}))
    )
    confirm.assert_called_once_with("Proceed?", default=False)
    select.assert_not_called()
    payload = json.loads(
        messages[0].content.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    )
    assert payload == {"confirm": answer}
    assert isinstance(payload["confirm"], bool)


@pytest.mark.parametrize("input_format", ["markdown", "native"])
@pytest.mark.parametrize("outcome", [None, KeyboardInterrupt(), EOFError()])
def test_execute_boolean_cancellation(
    monkeypatch: pytest.MonkeyPatch, input_format: str, outcome: object
) -> None:
    ask = Mock(side_effect=outcome if isinstance(outcome, BaseException) else None)
    ask.return_value = outcome
    confirm = Mock(return_value=SimpleNamespace(ask=ask))
    monkeypatch.setattr("questionary.confirm", confirm)
    monkeypatch.setattr(
        "questionary.select", Mock(side_effect=AssertionError("expected boolean"))
    )
    fields = "confirm: Proceed? [yes/no]"
    messages = (
        list(execute_form(fields, None, None))
        if input_format == "markdown"
        else list(execute_form(None, None, {"fields": fields}))
    )
    assert [message.content for message in messages] == ["Form cancelled"]


@pytest.mark.parametrize("input_format", ["markdown", "native"])
def test_execute_mixed_field_controls(
    monkeypatch: pytest.MonkeyPatch, input_format: str
) -> None:
    responses = iter(["demo", "12"])
    monkeypatch.setattr(
        "questionary.text", lambda *a, **k: SimpleNamespace(ask=lambda: next(responses))
    )
    select = Mock(return_value=SimpleNamespace(ask=lambda: "https://example.test"))
    monkeypatch.setattr("questionary.select", select)
    monkeypatch.setattr(
        "questionary.confirm", lambda *a, **k: SimpleNamespace(ask=lambda: False)
    )
    fields = "name: Name?\ncount: Count? (number)\nurl: Target [https://example.test, /tmp/data]\nconfirm: Proceed? [yes/no]"
    messages = (
        list(execute_form(fields, None, None))
        if input_format == "markdown"
        else list(execute_form(None, None, {"fields": fields}))
    )
    payload = json.loads(
        messages[0].content.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    )
    assert payload == {
        "name": "demo",
        "count": 12,
        "url": "https://example.test",
        "confirm": False,
    }
    select.assert_called_once_with(
        "Target", choices=["https://example.test", "/tmp/data"], use_shortcuts=True
    )
