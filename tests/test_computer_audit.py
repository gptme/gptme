"""Tests for the computer-use audit log (gptme/gptme#216).

Verifies that every ``computer()`` call is recorded (action, coordinate,
outcome) without ever persisting the raw ``text`` payload, and that failures
are both logged and still propagated to the caller.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

import gptme.tools.computer as computer_module
from gptme.tools.computer import COMPUTER_AUDIT_SUBDIR, computer

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def audit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(computer_module, "get_state_dir", lambda: tmp_path)
    return tmp_path / COMPUTER_AUDIT_SUBDIR


def _read_entries(audit_dir: Path) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = audit_dir / f"{today}.jsonl"
    assert path.exists(), f"expected audit log at {path}"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_successful_action_is_logged(
    audit_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        computer_module, "_computer_impl", lambda action, text, coordinate: None
    )
    computer("left_click", coordinate=(10, 20))

    entries = _read_entries(audit_dir)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "left_click"
    assert entry["coordinate"] == [10, 20]
    assert entry["error"] is None
    assert entry["text_len"] is None
    assert entry["duration_ms"] >= 0


def test_typed_text_is_never_logged_raw(
    audit_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        computer_module, "_computer_impl", lambda action, text, coordinate: None
    )
    secret = "hunter2-super-secret"
    computer("type", text=secret)

    entries = _read_entries(audit_dir)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["text_len"] == len(secret)
    assert secret not in json.dumps(entry)


def test_failed_action_is_logged_and_raised(
    audit_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(action, text, coordinate):
        raise ValueError("xdotool not found")

    monkeypatch.setattr(computer_module, "_computer_impl", _boom)

    with pytest.raises(ValueError, match="xdotool not found"):
        computer("screenshot")

    entries = _read_entries(audit_dir)
    assert len(entries) == 1
    assert entries[0]["error"] == "xdotool not found"


def test_multiple_actions_append_to_same_day_file(
    audit_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        computer_module, "_computer_impl", lambda action, text, coordinate: None
    )
    computer("screenshot")
    computer("cursor_position")

    entries = _read_entries(audit_dir)
    assert [e["action"] for e in entries] == ["screenshot", "cursor_position"]
