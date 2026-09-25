"""Tests for the anomaly_watchdog hooks.

Covers:
- scope_escape: write outside workspace triggers warning/block
- scope_escape: write inside workspace is allowed
- scope_escape: write inside allowed_dirs is allowed
- scope_escape: multi-file patches with any out-of-workspace section flagged
- write_storm: exceeding write limit triggers warning/block
- novel_host: browser call to new hostname triggers warning (incl. subtools)
- novel_host: browser call to trusted hostname is silent
- block mode: TOOL_CONFIRM hook actually skips the tool
- disabled mode: no anomalies fired
"""

from __future__ import annotations

import pytest

from .. import anomaly_watchdog
from ..anomaly_watchdog import (
    _check_novel_host,
    _check_scope_escape,
    _check_write_storm,
    _enabled,
    anomaly_watchdog_confirm,
    check_tool_post,
    check_tool_pre,
)
from ..types import ToolExecutePostData

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_tool_use(
    tool: str,
    args: list[str] | None = None,
    kwargs: dict[str, str] | None = None,
    content: str | None = None,
):
    """Build a minimal ToolUse-like object."""

    class _FakeToolUse:
        def __init__(self):
            self.tool = tool
            self.args = args
            self.kwargs = kwargs
            self.content = content

    return _FakeToolUse()


def _reset_storm_state() -> None:
    anomaly_watchdog._write_times_by_session.clear()


@pytest.fixture(autouse=True)
def _clean_storm_state():
    """Keep the module-level write window from leaking between tests."""
    _reset_storm_state()
    yield
    _reset_storm_state()


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
        tool_use = _fake_tool_use("save", args=["subdir/file.txt"])
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is None

    def test_write_outside_workspace_warned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        outside = tmp_path.parent / "other" / "file.txt"
        tool_use = _fake_tool_use("save", args=[str(outside)])
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is not None
        should_block, msg = result
        assert not should_block  # warn mode
        assert "scope_escape" in msg

    def test_write_outside_workspace_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        outside = tmp_path.parent / "bad" / "secret.txt"
        tool_use = _fake_tool_use("save", args=[str(outside)])
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is not None
        should_block, _ = result
        assert should_block

    def test_write_in_allowed_dir_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        allowed = tmp_path.parent / "allowed"
        allowed.mkdir(exist_ok=True)
        monkeypatch.setenv("GPTME_ANOMALY_ALLOWED_DIRS", str(allowed))
        tool_use = _fake_tool_use("save", args=[str(allowed / "ok.txt")])
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is None

    def test_no_workspace_skipped(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        tool_use = _fake_tool_use("save", args=["/etc/passwd"])
        result = _check_scope_escape(tool_use, None)
        assert result is None

    def test_patch_diff_header_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        diff_content = "--- a/old.txt\n+++ /etc/secret\n@@ -1 +1 @@\n+evil"
        tool_use = _fake_tool_use("patch", content=diff_content)
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is not None
        _, msg = result
        assert "scope_escape" in msg
        assert "secret" in msg

    def test_multi_file_patch_second_section_outside(self, tmp_path, monkeypatch):
        """A patch whose first file is inside the workspace but whose second
        file is outside must still be flagged (multi-file bypass fix)."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        outside = tmp_path.parent / "evil" / "escape.txt"
        diff_content = (
            "--- a/ok.txt\n"
            "+++ b/ok.txt\n"
            "@@ -1 +1 @@\n"
            "+fine\n"
            f"--- a/{outside.name}\n"
            f"+++ b/{outside}\n"
            "@@ -1 +1 @@\n"
            "+evil"
        )
        tool_use = _fake_tool_use("patch", content=diff_content)
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is not None
        _, msg = result
        assert "scope_escape" in msg

    def test_multi_file_patch_all_inside_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        diff_content = (
            "--- a/one.txt\n"
            "+++ b/one.txt\n"
            "@@ -1 +1 @@\n"
            "+x\n"
            "--- a/two.txt\n"
            "+++ b/two.txt\n"
            "@@ -1 +1 @@\n"
            "+y"
        )
        tool_use = _fake_tool_use("patch", content=diff_content)
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is None

    def test_deletion_only_patch_outside_is_flagged(self, tmp_path, monkeypatch):
        """A deletion-only hunk has ``+++ /dev/null`` as its target; the
        ``---`` header names the file that is actually removed."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        diff_content = (
            "--- /etc/passwd\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-root:x:0:0:root:/root:/bin/sh"
        )
        tool_use = _fake_tool_use("patch", content=diff_content)
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is not None
        _, msg = result
        assert "scope_escape" in msg

    def test_patch_checks_headers_alongside_path_arg(self, tmp_path, monkeypatch):
        """A patch call carries both a ``path`` argument and a diff body; the
        body's headers must not be skipped just because the argument exists."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        diff_content = "--- a/ok.txt\n+++ /etc/secret\n@@ -1 +1 @@\n-x\n+y"
        tool_use = _fake_tool_use(
            "patch", args=[str(tmp_path / "ok.txt")], content=diff_content
        )
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is not None
        _, msg = result
        assert "scope_escape" in msg

    def test_patch_path_arg_alone_is_checked(self, tmp_path, monkeypatch):
        """The path argument itself is still checked without diff content."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        tool_use = _fake_tool_use("patch", args=["/etc/secret"])
        result = _check_scope_escape(tool_use, tmp_path)
        assert result is not None
        assert "scope_escape" in result[1]

    def test_timestamped_headers_are_not_false_positives(self, tmp_path, monkeypatch):
        """GNU/git diffs append a tab-separated timestamp to header lines."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        diff_content = (
            "--- a/ok.txt\t2024-01-01 00:00:00 +0000\n"
            "+++ b/ok.txt\t2024-01-01 00:00:01 +0000\n"
            "@@ -1 +1 @@\n"
            "+fine"
        )
        tool_use = _fake_tool_use("patch", content=diff_content)
        assert _check_scope_escape(tool_use, tmp_path) is None


# ---------------------------------------------------------------------------
# write_storm
# ---------------------------------------------------------------------------


class TestWriteStorm:
    def test_below_limit_ok(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "5")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")
        for _ in range(4):
            anomaly_watchdog.record_write()
        assert _check_write_storm() is None

    def test_at_limit_triggers(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "3")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")
        for _ in range(3):
            anomaly_watchdog.record_write()
        result = _check_write_storm()
        assert result is not None
        _, msg = result
        assert "write_storm" in msg

    def test_warn_mode_counts_executed_writes(self, tmp_path, monkeypatch):
        """In warn mode nothing is blocked, so out-of-workspace writes that
        execute must still count toward the storm window."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "3")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)

        outside = _fake_tool_use("save", args=["/etc/shadow"])
        post = ToolExecutePostData(tool_use=outside, workspace=tmp_path)
        for _ in range(3):
            findings = anomaly_watchdog._detect_findings(outside, tmp_path)
            assert any("scope_escape" in f for f in findings)
            assert not any("write_storm" in f for f in findings)
            # Warn mode does not block, so the flagged write still executes.
            list(check_tool_post(post))
        # The fourth executed write reaches the limit and trips the storm.
        findings = anomaly_watchdog._detect_findings(outside, tmp_path)
        assert any("write_storm" in f for f in findings)

    def test_blocked_or_declined_writes_are_never_recorded(self, tmp_path, monkeypatch):
        """The window is advanced only after execution, so a call that is
        blocked by the watchdog (or declined by a later confirm hook) never
        counts toward it."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "3")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)

        outside = _fake_tool_use("save", args=["/etc/shadow"])
        for _ in range(5):
            findings = anomaly_watchdog._detect_findings(outside, tmp_path)
            assert any("scope_escape" in f for f in findings)
            assert not any("write_storm" in f for f in findings)

        # A sub-limit call that is declined before execution must not count
        # either: running the checks alone must never advance the window.
        inside = _fake_tool_use("save", args=[str(tmp_path / "ok.txt")])
        for _ in range(5):
            assert anomaly_watchdog._detect_findings(inside, tmp_path) == []
            assert _check_write_storm() is None

    def test_check_does_not_advance_the_window(self, monkeypatch):
        """Checking the storm must not record a write, or blocked attempts
        would refresh their own window and lock out later writes."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "3")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")

        for _ in range(10):
            assert _check_write_storm() is None
        assert anomaly_watchdog._write_times_by_session == {}

    def test_post_hook_records_only_write_tools(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "3")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")

        list(
            check_tool_post(
                ToolExecutePostData(
                    tool_use=_fake_tool_use("shell", args=["ls"]), workspace=tmp_path
                )
            )
        )
        assert anomaly_watchdog._write_times_by_session == {}

        list(
            check_tool_post(
                ToolExecutePostData(
                    tool_use=_fake_tool_use("save", args=["x.txt"]), workspace=tmp_path
                )
            )
        )
        assert (
            sum(len(v) for v in anomaly_watchdog._write_times_by_session.values()) == 1
        )

    def test_windows_are_isolated_per_session(self, monkeypatch):
        """Writes in one conversation must not count against another."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "3")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "60")

        current = {"key": "session-a"}
        monkeypatch.setattr(anomaly_watchdog, "_session_key", lambda: current["key"])

        # Fill session-a's window to the limit, then hand off to session-b.
        for _ in range(3):
            anomaly_watchdog.record_write()
        assert _check_write_storm() is not None  # session-a exceeds its limit
        # session-b starts empty and must not inherit session-a's window.
        current["key"] = "session-b"
        for _ in range(3):
            assert _check_write_storm() is None
            anomaly_watchdog.record_write()

    def test_empty_windows_are_evicted(self, monkeypatch):
        """A conversation's window key must not linger once its timestamps
        age out, or a long-lived server accumulates one key per session."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_LIMIT", "5")
        monkeypatch.setenv("GPTME_ANOMALY_WRITE_WINDOW", "10")

        now = {"t": 1000.0}
        monkeypatch.setattr(anomaly_watchdog.time, "monotonic", lambda: now["t"])
        current = {"key": "session-a"}
        monkeypatch.setattr(anomaly_watchdog, "_session_key", lambda: current["key"])

        anomaly_watchdog.record_write()
        assert "session-a" in anomaly_watchdog._write_times_by_session

        # Switch conversations and let session-a's window age out.
        current["key"] = "session-b"
        now["t"] += 11
        assert _check_write_storm() is None
        assert "session-a" not in anomaly_watchdog._write_times_by_session


# ---------------------------------------------------------------------------
# novel_host
# ---------------------------------------------------------------------------


class TestNovelHost:
    def test_new_host_warns(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_HOSTS", raising=False)
        tool_use = _fake_tool_use("browser", args=["https://evil.example.com/x"])
        result = _check_novel_host(tool_use)
        assert result is not None
        _, msg = result
        assert "novel_host" in msg
        assert "evil.example.com" in msg

    def test_browser_subtool_warns(self, monkeypatch):
        """Subtools invoked as 'browser.read_url' must not bypass host checks."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_HOSTS", raising=False)
        tool_use = _fake_tool_use(
            "browser.read_url", kwargs={"url": "https://sneaky.example.org"}
        )
        result = _check_novel_host(tool_use)
        assert result is not None
        _, msg = result
        assert "novel_host" in msg

    def test_trusted_host_silent(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.setenv("GPTME_ANOMALY_ALLOWED_HOSTS", "docs.example.com")
        tool_use = _fake_tool_use("browser", args=["https://docs.example.com/page"])
        result = _check_novel_host(tool_use)
        assert result is None

    def test_localhost_silent(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_HOSTS", raising=False)
        tool_use = _fake_tool_use("browser", args=["http://localhost:8080/api"])
        result = _check_novel_host(tool_use)
        assert result is None

    def test_no_url_skipped(self, monkeypatch):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        tool_use = _fake_tool_use("browser", args=None, content=None)
        result = _check_novel_host(tool_use)
        assert result is None


# ---------------------------------------------------------------------------
# check_tool_pre (integration)
# ---------------------------------------------------------------------------


class TestCheckToolPre:
    def test_disabled_no_output(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "off")
        tool_use = _fake_tool_use("save", args=["/etc/passwd"])
        msgs = list(check_tool_pre(_pre_data(tool_use, tmp_path)))
        assert msgs == []

    def test_scope_escape_warn_yields_message(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        tool_use = _fake_tool_use("save", args=["/etc/passwd"])
        msgs = list(check_tool_pre(_pre_data(tool_use, tmp_path)))
        assert len(msgs) == 1
        assert "scope_escape" in msgs[0].content

    def test_block_mode_pre_yields_no_stoppropagation(self, monkeypatch, tmp_path):
        """In block mode the TOOL_EXECUTE_PRE hook must not pretend to block —
        actual blocking happens in the TOOL_CONFIRM hook."""
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        tool_use = _fake_tool_use("save", args=["/etc/passwd"])
        msgs = list(check_tool_pre(_pre_data(tool_use, tmp_path)))
        assert msgs == []


# ---------------------------------------------------------------------------
# anomaly_watchdog_confirm (block mode enforcement)
# ---------------------------------------------------------------------------


def _pre_data(tool_use, workspace):
    class _FakeData:
        def __init__(self):
            self.tool_use = tool_use
            self.workspace = workspace
            self.log = None

    return _FakeData()


class TestConfirmHook:
    def test_block_mode_skips_flagged_tool(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        tool_use = _fake_tool_use("save", args=["/etc/passwd"])
        result = anomaly_watchdog_confirm(tool_use, workspace=tmp_path)
        assert result is not None
        assert result.action.value == "skip"
        assert "anomaly_watchdog" in (result.message or "")

    def test_block_mode_allows_clean_tool(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "block")
        monkeypatch.delenv("GPTME_ANOMALY_ALLOWED_DIRS", raising=False)
        tool_use = _fake_tool_use("save", args=["inside.txt"])
        assert anomaly_watchdog_confirm(tool_use, workspace=tmp_path) is None

    def test_warn_mode_confirm_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "warn")
        tool_use = _fake_tool_use("save", args=["/etc/passwd"])
        assert anomaly_watchdog_confirm(tool_use, workspace=tmp_path) is None

    def test_off_mode_confirm_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GPTME_ANOMALY_WATCHDOG", "off")
        tool_use = _fake_tool_use("save", args=["/etc/passwd"])
        assert anomaly_watchdog_confirm(tool_use, workspace=tmp_path) is None


# ---------------------------------------------------------------------------
# config activation
# ---------------------------------------------------------------------------


class TestConfigActivation:
    def test_config_section_enables_warn(self, monkeypatch):
        monkeypatch.delenv("GPTME_ANOMALY_WATCHDOG", raising=False)

        class _Cfg:
            class user:
                plugin = {"anomaly_watchdog": {"mode": "warn"}}

            project = None

        anomaly_watchdog._init_from_config(_Cfg())
        import os

        assert os.environ["GPTME_ANOMALY_WATCHDOG"] == "warn"

    @pytest.mark.parametrize("configured", ["block", "off"])
    def test_config_mode_is_not_downgraded_to_warn(self, monkeypatch, configured):
        """A configured ``mode`` must survive config activation unchanged."""
        monkeypatch.delenv("GPTME_ANOMALY_WATCHDOG", raising=False)

        class _Cfg:
            class user:
                plugin = {"anomaly_watchdog": {"mode": configured}}

            project = None

        anomaly_watchdog._init_from_config(_Cfg())
        import os

        assert os.environ["GPTME_ANOMALY_WATCHDOG"] == configured

    def test_config_without_mode_defaults_to_warn(self, monkeypatch):
        monkeypatch.delenv("GPTME_ANOMALY_WATCHDOG", raising=False)

        class _Cfg:
            class user:
                plugin = {"anomaly_watchdog": {"write_limit": 5}}

            project = None

        anomaly_watchdog._init_from_config(_Cfg())
        import os

        assert os.environ["GPTME_ANOMALY_WATCHDOG"] == "warn"
        assert os.environ["GPTME_ANOMALY_WRITE_LIMIT"] == "5"
