"""Tests for the anomaly_watchdog TOOL_EXECUTE_PRE hook.

Covers:
- scope_escape: write outside workspace triggers warning/block
- scope_escape: write inside workspace is allowed
- scope_escape: write inside allowed_dirs is allowed
- write_storm: exceeding write limit triggers warning/block
- novel_host: browser call to new hostname triggers warning
- novel_host: browser call to trusted hostname is silent
- disabled mode: no anomalies fired
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ..anomaly_watchdog import (
    _check_novel_host,
    _check_scope_escape,
    _check_write_storm,
    _enabled,
    check_tool_pre,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_data(
    tool: str,
    args: list[str] | None = None,
    kwargs: dict[str, str] | None = None,
    content: str | None = None,
    workspace: Path | None = None,
):
    """Build a minimal ToolExecutePreData-like object."""

    class _FakeToolUse:
        def __init__(self):
            self.tool = tool
            self.args = args
            self.kwargs = kwargs
            self.content = content

    class _FakeData:
        def __init__(self):
            self.tool_use = _FakeToolUse()
            self.workspace = workspace
            self.log = None

    return _FakeData()


# ---------------------------------------------------------------------------
# _enabled
# ---------------------------------------------------------------------------


class TestEnabled:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GPTME_ANOMALY_WATCHDOG", raising=False)
        assert not _enabled()

    def test_warn_mode(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        assert _enabled()

    def test_block_mode(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        assert _enabled()

    def test_off_explicit(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "off")
        assert not _enabled()


# ---------------------------------------------------------------------------
# scope_escape
# ---------------------------------------------------------------------------


class TestScopeEscape:
    def test_write_inside_workspace_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        data = _fake_data("save", args=["subdir/file.txt"], workspace=tmp_path)
        result = _check_scope_escape(data)
        assert result is None

    def test_write_outside_workspace_warned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        outside = tmp_path.parent / "other" / "file.txt"
        data = _fake_data("save", args=[str(outside)], workspace=tmp_path)
        result = _check_scope_escape(data)
        assert result is not None
        should_block, msg = result
        assert not should_block  # warn mode
        assert "scope_escape" in msg

    def test_write_outside_workspace_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        outside = tmp_path.parent / "bad" / "secret.txt"
        data = _fake_data("save", args=[str(outside)], workspace=tmp_path)
        result = _check_scope_escape(data)
        assert result is not None
        should_block, _ = result
        assert should_block

    def test_write_in_allowed_dir_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        allowed = tmp_path.parent / "allowed"
        allowed.mkdir(exist_ok=True)
        monkeypatch.setenv("GPTME_ANOMALY_ALLOWED_DIRS", str(allowed))
        data = _fake_data("save", args=[str(allowed / "ok.txt")], workspace=tmp_path)
        result = _check_scope_escape(data)
        assert result is None

    def test_no_workspace_skipped(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        data = _fake_data("save", args=["/etc/passwd"], workspace=None)
        result = _check_scope_escape(data)
        assert result is None

    def test_patch_diff_header_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        diff_content = "--- a/old.txt\n+++ b//etc/secret\n@@ -1 +1 @@\n+evil"
        data = _fake_data("patch", content=diff_content, workspace=tmp_path)
        result = _check_scope_escape(data)
        assert result is not None
        _, msg = result
        assert "scope_escape" in msg


# ---------------------------------------------------------------------------
# write_storm
# ---------------------------------------------------------------------------


class TestWriteStorm:
    def test_below_limit_ok(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "5")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")
        from ..anomaly_watchdog import _write_times_var

        _write_times_var.set([])  # reset
        for _ in range(4):
            result = _check_write_storm()
        assert result is None

    def test_at_limit_triggers(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "3")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")
        from ..anomaly_watchdog import _write_times_var

        _write_times_var.set([])  # reset
        result = None
        for _ in range(4):
            result = _check_write_storm()
        assert result is not None
        _, msg = result
        assert "write_storm" in msg


# ---------------------------------------------------------------------------
# novel_host
# ---------------------------------------------------------------------------


class TestNovelHost:
    def test_new_host_warns(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_HOSTS", raising=False)
        data = _fake_data("browser", args=["https://evil.example.com/x"])
        result = _check_novel_host(data)
        assert result is not None
        _, msg = result
        assert "novel_host" in msg
        assert "evil.example.com" in msg

    def test_trusted_host_silent(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_ALLOWED_HOSTS", "docs.example.com")
        data = _fake_data("browser", args=["https://docs.example.com/page"])
        result = _check_novel_host(data)
        assert result is None

    def test_localhost_silent(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_HOSTS", raising=False)
        data = _fake_data("browser", args=["http://localhost:8080/api"])
        result = _check_novel_host(data)
        assert result is None

    def test_no_url_skipped(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        data = _fake_data("browser", args=None, content=None)
        result = _check_novel_host(data)
        assert result is None


# ---------------------------------------------------------------------------
# check_tool_pre (integration)
# ---------------------------------------------------------------------------


class TestCheckToolPre:
    def test_disabled_no_output(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "off")
        data = _fake_data("save", args=["/etc/passwd"], workspace=tmp_path)
        msgs = list(check_tool_pre(data))
        assert msgs == []

    def test_scope_escape_warn_yields_message(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        data = _fake_data("save", args=["/etc/passwd"], workspace=tmp_path)
        msgs = list(check_tool_pre(data))
        assert len(msgs) == 1  # Message only, no StopPropagation

    def test_scope_escape_block_yields_stop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        from ..types import StopPropagation

        data = _fake_data("save", args=["/etc/passwd"], workspace=tmp_path)
        msgs = list(check_tool_pre(data))
        assert any(isinstance(m, StopPropagation) for m in msgs)
