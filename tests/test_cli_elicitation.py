"""Exercise native tool JSON through the CLI, with both selection backends."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gptme.hooks.elicitation import ElicitationResponse, cli_elicit
from gptme.tools.elicit import parse_elicitation_spec


@pytest.fixture(params=["questionary", "fallback"])
def backend(request, monkeypatch):
    """Keep terminal I/O synthetic, but run the real parser and handlers."""
    confirm = Mock(
        side_effect=lambda prompt, **kw: SimpleNamespace(ask=lambda: kw["default"])
    )
    select = Mock(
        side_effect=lambda prompt, **kw: SimpleNamespace(
            ask=lambda: kw.get("default", kw["choices"][0])
        )
    )
    module = SimpleNamespace(confirm=confirm, select=select)
    monkeypatch.setitem(
        sys.modules, "questionary", module if request.param == "questionary" else None
    )
    return request.param, module


def run_spec(spec: dict) -> ElicitationResponse:
    request = parse_elicitation_spec(json.dumps(spec))
    assert request is not None
    return cli_elicit(request)


def form(fields: list[dict]) -> ElicitationResponse:
    return run_spec({"type": "form", "prompt": "Synthetic setup", "fields": fields})


def submitted(response: ElicitationResponse) -> dict:
    assert not response.cancelled
    assert response.value is not None
    return json.loads(response.value)


def inputs(monkeypatch, answers: list[str | BaseException]) -> None:
    remaining = iter(answers)

    def answer(prompt: str) -> str:
        value = next(remaining, EOFError())
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", answer)


@pytest.mark.parametrize(
    ("default", "expected"),
    [
        (None, "yes"),
        (True, "yes"),
        (False, "no"),
        ("no", "no"),
        (" FALSE ", "no"),
        ("0", "no"),
        ("n", "no"),
        ("yes", "yes"),
        ("true", "yes"),
        ("1", "yes"),
        ("Y", "yes"),
    ],
)
def test_confirmation_defaults(backend, monkeypatch, default, expected):
    inputs(monkeypatch, [""])
    response = run_spec(
        {"type": "confirmation", "prompt": "Continue?", "default": default}
    )
    assert not response.cancelled
    assert response.value == expected


@pytest.mark.parametrize("default", ["maybe", "", 0, 1, [], {}])
def test_unsupported_confirmation_default_cancels(backend, monkeypatch, default):
    inputs(monkeypatch, [""])
    response = run_spec(
        {"type": "confirmation", "prompt": "Continue?", "default": default}
    )
    assert response.cancelled
    assert response.value is None


@pytest.mark.parametrize(("answer", "expected"), [("yes", "yes"), ("no", "no")])
def test_confirmation_explicit_override(backend, monkeypatch, answer, expected):
    name, module = backend
    inputs(monkeypatch, [answer])
    module.confirm.side_effect = lambda *a, **kw: SimpleNamespace(
        ask=lambda: answer == "yes"
    )
    response = run_spec(
        {"type": "confirmation", "prompt": "Continue?", "default": expected == "no"}
    )
    assert response.value == expected


def test_fallback_invalid_confirmation_retries(monkeypatch):
    monkeypatch.setitem(sys.modules, "questionary", None)
    inputs(monkeypatch, ["maybe", "no"])
    assert run_spec({"type": "confirmation", "prompt": "Continue?"}).value == "no"


@pytest.mark.parametrize("default", [False, "false", "no", "0"])
def test_boolean_form_default(backend, monkeypatch, default):
    inputs(monkeypatch, [""])
    response = form(
        [{"name": "tests", "prompt": "Tests?", "type": "boolean", "default": default}]
    )
    assert submitted(response) == {"tests": False}


@pytest.mark.parametrize(
    ("answers", "default", "expected"),
    [
        (["", "  ", "alice"], None, "alice"),
        ([""], "guest", "guest"),
        (["", "bob"], "   ", "bob"),
    ],
)
def test_required_text_retries_or_uses_valid_default(
    backend, monkeypatch, answers, default, expected
):
    inputs(monkeypatch, answers)
    response = form([{"name": "name", "prompt": "Name?", "default": default}])
    assert submitted(response) == {"name": expected}


@pytest.mark.parametrize(
    ("answer", "expected"), [("3", 3), ("-2", -2), ("2.5", 2.5), ("1e3", 1000.0)]
)
def test_numeric_values(backend, monkeypatch, answer, expected):
    inputs(monkeypatch, [answer])
    response = form([{"name": "count", "prompt": "Count?", "type": "number"}])
    value = submitted(response)["count"]
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize("invalid", ["garbage", "", "   ", "nan", "inf", "1e999"])
def test_invalid_number_retries(backend, monkeypatch, invalid):
    inputs(monkeypatch, [invalid, "4"])
    response = form([{"name": "count", "prompt": "Count?", "type": "number"}])
    assert submitted(response) == {"count": 4}


@pytest.mark.parametrize(("default", "expected"), [(0, 0), (2.5, 2.5), ("3", 3)])
def test_numeric_default(backend, monkeypatch, default, expected):
    inputs(monkeypatch, [""])
    response = form(
        [{"name": "count", "prompt": "Count?", "type": "number", "default": default}]
    )
    assert submitted(response) == {"count": expected}


def test_optional_blanks(backend, monkeypatch):
    inputs(monkeypatch, ["  ", ""])
    response = form(
        [
            {"name": "note", "prompt": "Note?", "required": False},
            {"name": "count", "prompt": "Count?", "type": "number", "required": False},
        ]
    )
    assert submitted(response) == {"note": "", "count": None}


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_cancel_does_not_return_partial_form(backend, monkeypatch, error):
    inputs(monkeypatch, ["alice", "invalid", error])
    response = form(
        [
            {"name": "name", "prompt": "Name?"},
            {"name": "count", "prompt": "Count?", "type": "number"},
        ]
    )
    assert response.cancelled
    assert response.value is None


def test_questionary_cancel_discards_partial_form(backend, monkeypatch):
    name, module = backend
    if name == "fallback":
        pytest.skip("questionary-specific cancellation")
    inputs(monkeypatch, ["alice"])
    module.confirm.side_effect = lambda *a, **kw: SimpleNamespace(ask=lambda: None)
    response = form(
        [
            {"name": "name", "prompt": "Name?"},
            {"name": "tests", "prompt": "Tests?", "type": "boolean"},
        ]
    )
    assert response.cancelled
    assert response.value is None


def test_multifield_defaults_and_secret_redaction(backend, monkeypatch, capsys):
    inputs(monkeypatch, ["", "", ""])
    secrets = iter(["", "synthetic-secret"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(secrets))
    response = form(
        [
            {"name": "name", "prompt": "Name?", "default": "demo"},
            {
                "name": "language",
                "prompt": "Language?",
                "type": "choice",
                "options": ["python", "rust"],
                "default": "rust",
            },
            {"name": "token", "prompt": "Token?", "type": "secret"},
        ]
    )
    assert submitted(response) == {
        "name": "demo",
        "language": "rust",
        "token": "<secret provided>",
    }
    assert "synthetic-secret" not in capsys.readouterr().out


def test_secret_default_is_used_without_display(backend, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", Mock(side_effect=["", EOFError()]))
    response = form(
        [
            {
                "name": "token",
                "prompt": "Token?",
                "type": "secret",
                "default": "synthetic-preset",
            }
        ]
    )
    assert submitted(response) == {"token": "<secret provided>"}
    assert "synthetic-preset" not in capsys.readouterr().out


@pytest.mark.parametrize("default", ["bad-number", " "])
def test_invalid_numeric_default_retries(backend, monkeypatch, default):
    inputs(monkeypatch, ["", "2"])
    response = form(
        [{"name": "count", "prompt": "Count?", "type": "number", "default": default}]
    )
    assert submitted(response) == {"count": 2}
