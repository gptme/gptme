"""Tests for aw_watcher_agent lifecycle hook integration."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from gptme.hooks.aw_watcher_agent import emit_end, emit_start
from gptme.logmanager import LogManager


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_emit_start_shells_out_when_enabled(monkeypatch):
    """session.start should emit a best-effort aw-watcher-agent CLI call."""
    monkeypatch.setenv("GPTME_AW_WATCHER_AGENT", "1")
    model = SimpleNamespace(full="anthropic/test-model")

    with (
        patch("gptme.hooks.aw_watcher_agent.get_default_model", return_value=model),
        patch(
            "gptme.hooks.aw_watcher_agent.subprocess.run",
            return_value=_completed(),
        ) as run,
    ):
        assert list(emit_start(Path("/tmp/conv123"), Path("/tmp/workspace"), [])) == []

    argv = run.call_args.args[0]
    assert argv[:2] == ["aw-watcher-agent", "emit-start"]
    assert "--session-id" in argv and "conv123" in argv
    assert "--workspace" in argv and "workspace" in argv
    assert "--model" in argv and "anthropic/test-model" in argv


def test_emit_end_shells_out_when_enabled(monkeypatch):
    """session.end should emit the matching aw-watcher-agent close event."""
    monkeypatch.setenv("GPTME_AW_WATCHER_AGENT", "1")

    manager = cast(
        LogManager,
        SimpleNamespace(
            logdir=Path("/tmp/conv123"),
            workspace=Path("/tmp/workspace"),
        ),
    )

    with patch(
        "gptme.hooks.aw_watcher_agent.subprocess.run",
        return_value=_completed(),
    ) as run:
        assert list(emit_end(manager)) == []

    argv = run.call_args.args[0]
    assert argv[:2] == ["aw-watcher-agent", "emit-end"]
    assert "--session-id" in argv and "conv123" in argv


def test_emit_start_noops_when_disabled(monkeypatch):
    """No subprocess call should happen when the plugin is disabled."""
    monkeypatch.delenv("GPTME_AW_WATCHER_AGENT", raising=False)

    with patch("gptme.hooks.aw_watcher_agent.subprocess.run") as run:
        assert list(emit_start(Path("/tmp/conv123"), Path("/tmp/workspace"), [])) == []

    run.assert_not_called()
