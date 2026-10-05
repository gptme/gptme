"""gptme-util hooks must reject malformed Claude Code settings.json cleanly."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_hooks import hooks

BAD = [
    b"\xff\xfe{",
    b"[]",
    b"null",
    b'{"hooks": []}',
    b'{"hooks": {"UserPromptSubmit": "x"}}',
    b'{"hooks": {"PreToolUse": [1]}}',
    b'{"hooks": {"PreToolUse": [{"hooks": "x"}]}}',
    b'{"hooks": {"PreToolUse": [{"hooks": [{"command": null}]}]}}',
    b'{"hooks": {"UserPromptSubmit": [{"hooks": [{"command": 1}]}]}}',
]


@pytest.mark.parametrize("body", BAD)
@pytest.mark.parametrize("cmd", ["install", "uninstall"])
def test_malformed_settings_is_clean_error(tmp_path: Path, body: bytes, cmd: str):
    (tmp_path / "gptme.toml").write_text("")
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_bytes(body)
    result = CliRunner().invoke(hooks, [cmd, "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert settings.read_bytes() == body  # never rewritten


@pytest.mark.parametrize("body", BAD)
def test_status_reports_malformed_settings(tmp_path: Path, body: bytes, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_bytes(body)
    result = CliRunner().invoke(hooks, ["status", "--workspace", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "unusable" in result.output


def test_valid_settings_roundtrip(tmp_path: Path):
    (tmp_path / "gptme.toml").write_text("")
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"other": 1}))
    r = CliRunner().invoke(hooks, ["install", "--workspace", str(tmp_path)])
    assert r.exit_code == 0, r.output
    data = json.loads(settings.read_text())
    assert data["other"] == 1 and "UserPromptSubmit" in data["hooks"]
    r = CliRunner().invoke(hooks, ["uninstall", "--workspace", str(tmp_path)])
    assert r.exit_code == 0, r.output
