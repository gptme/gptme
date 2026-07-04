"""Tests for real-time risk-label streaming in computer-use actions (#216).

Validates that _stream_action_risk():
- Emits the correct action name and risk level via notify_progress()
- Only fires when get_current_agent_id() returns a non-None agent id
- Is a no-op outside of a subagent context (no agent id)
- Includes coordinate in the record when provided
- Includes text_len (byte-length, not raw text) for sensitive actions
- Silently swallows all exceptions to avoid breaking the action

Gate-ordering guarantee (Greptile P1):
- Streaming fires AFTER sensitive_action_gate(), so blocked actions never emit
  a progress record to the parent agent.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gptme.tools._computer_gate import action_risk_level
from gptme.tools.computer import _stream_action_risk, computer

# ---------------------------------------------------------------------------
# Risk-level classification (canonical source is _computer_gate.py)
# ---------------------------------------------------------------------------


def test_action_risk_level_read():
    assert action_risk_level("screenshot") == "read"
    assert action_risk_level("accessibility_tree") == "read"
    assert action_risk_level("observe_desktop") == "read"


def test_action_risk_level_write():
    assert action_risk_level("left_click") == "write"
    assert action_risk_level("mouse_move") == "write"
    assert action_risk_level("window_focus") == "write"


def test_action_risk_level_sensitive():
    assert action_risk_level("type") == "sensitive"
    assert action_risk_level("key") == "sensitive"
    assert action_risk_level("left_click_drag") == "sensitive"


def test_action_risk_level_unknown_defaults_to_write():
    assert action_risk_level("totally_unknown_action") == "write"


# ---------------------------------------------------------------------------
# _stream_action_risk: no-op outside subagent
# ---------------------------------------------------------------------------


def test_no_emit_outside_subagent():
    """When get_current_agent_id() returns None, notify_progress is never called."""
    mock_np = MagicMock()
    subagent_mod = MagicMock()
    subagent_mod.get_current_agent_id.return_value = None  # not inside a subagent
    subagent_mod.notify_progress = mock_np
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("screenshot")
    mock_np.assert_not_called()


# ---------------------------------------------------------------------------
# _stream_action_risk: emits records inside a subagent
# ---------------------------------------------------------------------------


def _parse_progress_call(mock_np) -> dict:
    """Extract and parse the JSON record from a notify_progress() call."""
    assert mock_np.call_count == 1, f"expected 1 call, got {mock_np.call_count}"
    _agent_id, message = mock_np.call_args[0]
    assert message.startswith("action:"), f"unexpected prefix: {message!r}"
    return json.loads(message[len("action:") :])


def _make_subagent_module(agent_id: str, mock_np: MagicMock) -> MagicMock:
    mod = MagicMock()
    mod.get_current_agent_id.return_value = agent_id
    mod.notify_progress = mock_np
    return mod


@pytest.fixture
def agent_id():
    return "test-agent-42"


def test_emits_read_action(agent_id):
    mock_np = MagicMock()
    subagent_mod = _make_subagent_module(agent_id, mock_np)
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("screenshot")
    record = _parse_progress_call(mock_np)
    assert record["action"] == "screenshot"
    assert record["risk"] == "read"
    assert "coord" not in record
    assert "text_len" not in record


def test_emits_write_action_with_coordinate(agent_id):
    mock_np = MagicMock()
    subagent_mod = _make_subagent_module(agent_id, mock_np)
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("left_click", coordinate=(640, 480))
    record = _parse_progress_call(mock_np)
    assert record["action"] == "left_click"
    assert record["risk"] == "write"
    assert record["coord"] == [640, 480]
    assert "text_len" not in record


def test_emits_sensitive_action_with_text_len_not_text(agent_id):
    """Sensitive actions include text length but never the raw text content."""
    mock_np = MagicMock()
    subagent_mod = _make_subagent_module(agent_id, mock_np)
    secret = "mysecretpassword"
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("type", text=secret)
    record = _parse_progress_call(mock_np)
    assert record["action"] == "type"
    assert record["risk"] == "sensitive"
    assert record["text_len"] == len(secret.encode())
    # Raw content must never appear in the emitted record
    assert secret not in json.dumps(record)


def test_emits_sensitive_action_without_text(agent_id):
    """Sensitive action with no text: text_len is absent (not None)."""
    mock_np = MagicMock()
    subagent_mod = _make_subagent_module(agent_id, mock_np)
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("key")
    record = _parse_progress_call(mock_np)
    assert record["action"] == "key"
    assert record["risk"] == "sensitive"
    assert "text_len" not in record


def test_passes_agent_id_to_notify_progress(agent_id):
    """notify_progress receives the correct agent_id as its first argument."""
    mock_np = MagicMock()
    subagent_mod = _make_subagent_module(agent_id, mock_np)
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("left_click")
    called_agent_id, _msg = mock_np.call_args[0]
    assert called_agent_id == agent_id


# ---------------------------------------------------------------------------
# _stream_action_risk: exception safety
# ---------------------------------------------------------------------------


def test_exception_in_notify_progress_is_swallowed(agent_id):
    """An exception inside notify_progress must never propagate to the caller."""
    mock_np = MagicMock(side_effect=RuntimeError("queue full"))
    subagent_mod = _make_subagent_module(agent_id, mock_np)
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("left_click")  # must not raise


def test_exception_in_get_current_agent_id_is_swallowed():
    """An exception in get_current_agent_id must never propagate."""
    mock_np = MagicMock()
    subagent_mod = MagicMock()
    subagent_mod.get_current_agent_id.side_effect = RuntimeError("thread-local error")
    subagent_mod.notify_progress = mock_np
    with patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}):
        _stream_action_risk("screenshot")  # must not raise
    mock_np.assert_not_called()


# ---------------------------------------------------------------------------
# Gate-ordering guarantee: blocked actions must not emit a progress record
# ---------------------------------------------------------------------------


def test_gate_blocked_action_does_not_emit_progress_record(agent_id):
    """When sensitive_action_gate raises PermissionError, no streaming record is sent.

    This is the P1 ordering fix: _stream_action_risk() must fire AFTER the gate
    check so a blocked action never delivers a misleading progress record to the
    parent agent claiming the action was performed.
    """
    mock_np = MagicMock()
    subagent_mod = _make_subagent_module(agent_id, mock_np)

    # Simulate a non-interactive session with gate enabled — sensitive actions blocked
    env_override = {"GPTME_COMPUTER_CONFIRM_SENSITIVE": "1"}
    with (
        patch.dict("sys.modules", {"gptme.tools.subagent": subagent_mod}),
        patch.dict("os.environ", env_override),
        patch("sys.stdin.isatty", return_value=False),
        pytest.raises(PermissionError),
    ):
        computer("type", text="secret")

    # No progress record should have been emitted for the blocked action
    mock_np.assert_not_called()
