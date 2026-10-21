"""Native tool calls deliver input as ``kwargs`` with ``code`` and ``args`` empty.

A tool that reads only ``code``/``args`` silently drops native calls (see
gptme/gptme#4463). These tests call each tool the way a native call arrives.
"""

import json

import pytest

from gptme.hooks.elicitation import ElicitationResponse
from gptme.tools import elicit as elicit_mod
from gptme.tools import form as form_mod
from gptme.tools.elicit import execute_elicit
from gptme.tools.form import execute_form
from gptme.tools.mcp import execute_mcp
from gptme.tools.progress import execute_progress
from gptme.tools.vent import _vent_this_turn, execute_vent


@pytest.fixture()
def ledger_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    _vent_this_turn.set(False)
    yield tmp_path / "gptme" / "friction-ledger.jsonl"
    _vent_this_turn.set(False)


def test_vent_native_message_and_owner(ledger_path):
    msg = execute_vent(
        None, None, {"message": "Stuck on X", "resolution_owner": "tooling"}
    )
    assert "recorded" in msg.content.lower()
    entry = json.loads(ledger_path.read_text().strip())
    assert entry["message"] == "Stuck on X"
    assert entry["resolution_owner"] == "tooling"


def test_vent_native_message_only(ledger_path):
    execute_vent(None, None, {"message": "Stuck on Y"})
    entry = json.loads(ledger_path.read_text().strip())
    assert entry["message"] == "Stuck on Y"
    assert "resolution_owner" not in entry


def test_progress_native_message():
    msgs = list(execute_progress(None, None, {"message": "halfway there"}))
    # Not running as a subagent, so delivery fails -- but not as an empty block.
    assert "empty" not in msgs[0].content.lower()


def test_mcp_native_command():
    msgs = list(execute_mcp(None, None, {"command": "no-such-command"}))
    assert "Unknown MCP command: no-such-command" in msgs[0].content


def test_form_native_fields(monkeypatch):
    seen: list[str] = []

    def fake_parse(content: str) -> list[dict]:
        seen.append(content)
        return []

    monkeypatch.setattr(form_mod, "parse_form_content", fake_parse)
    list(execute_form(None, None, {"fields": "name: Your name"}))
    assert seen == ["name: Your name"]


@pytest.mark.parametrize(
    "spec",
    [
        '{"type": "text", "prompt": "Name?"}',
        # json_repair can hand over the spec as an object rather than a string
        {"type": "text", "prompt": "Name?"},
    ],
)
def test_elicit_native_spec(monkeypatch, spec):
    seen = []

    def fake_elicit(request):
        seen.append(request)
        return ElicitationResponse(value="Bob", cancelled=False)

    monkeypatch.setattr(elicit_mod, "elicit", fake_elicit)
    msgs = list(execute_elicit(None, None, {"spec": spec}))
    assert len(seen) == 1
    assert seen[0].prompt == "Name?"
    assert "Bob" in msgs[-1].content
