"""Tests for the verify_claim tool."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from gptme.tools import clear_tools, get_available_tools, set_tools
from gptme.tools.base import ToolSpec
from gptme.tools.verify import (
    PROCESS_VERIFICATION_ENV,
    execute_verify_claim,
    tool,
    verify_claim,
    verify_contains,
    verify_env_var,
    verify_file_exists,
    verify_file_not_exists,
    verify_not_contains,
    verify_shell,
    verify_test_fails,
    verify_test_passes,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_verify_tool_is_discoverable() -> None:
    available = get_available_tools(include_mcp=False)

    assert tool in available
    assert any(candidate.name == "verify_claim" for candidate in available)


def test_verify_file_exists_branches(tmp_path: Path) -> None:
    present = tmp_path / "present.txt"
    missing = tmp_path / "missing.txt"
    present.write_text("ok\n", encoding="utf-8")

    assert verify_file_exists(str(present)).ok is True
    assert verify_file_exists(str(missing)).ok is False
    assert verify_file_not_exists(str(missing)).ok is True
    assert verify_file_not_exists(str(present)).ok is False


def test_verify_contains_branches(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nbeta\n", encoding="utf-8")

    found = verify_contains(str(path), "alp.a")
    missing = verify_contains(str(path), "gamma")
    absent = verify_not_contains(str(path), "gamma")
    present = verify_not_contains(str(path), "beta")

    assert found.ok is True
    assert found.actual == "line 1: alpha"
    assert missing.ok is False
    assert absent.ok is True
    assert present.ok is False
    assert present.actual == "line 2: beta"


def test_path_traversal_via_relative_is_denied(tmp_path: Path, monkeypatch) -> None:
    # A relative path that resolves outside cwd must be blocked even though
    # `resolved` is always absolute — the old `elif not resolved.is_absolute()`
    # was unreachable. This test ensures the fix holds.
    monkeypatch.chdir(tmp_path)
    result = verify_file_exists("../../etc/passwd")
    assert result.ok is False
    assert "path traversal denied" in result.reason


def test_verify_contains_reports_invalid_regex(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("alpha\n", encoding="utf-8")

    result = verify_contains(str(path), "[")

    assert result.ok is False
    assert "invalid regex pattern" in result.reason


def test_verify_env_var_does_not_leak_actual_value(monkeypatch) -> None:
    monkeypatch.setenv("GPTME_VERIFY_TEST_SECRET", "actual-secret")

    present = verify_env_var("GPTME_VERIFY_TEST_SECRET")
    mismatch = verify_env_var("GPTME_VERIFY_TEST_SECRET", expected="expected-secret")
    missing = verify_env_var("GPTME_VERIFY_TEST_MISSING")

    assert present.ok is True
    assert present.actual == "<set>"
    assert mismatch.ok is False
    assert mismatch.actual == "<set but different>"
    assert "actual-secret" not in str(mismatch)
    assert missing.ok is False
    assert missing.actual == "<unset>"


def test_process_checks_require_shell_tool_or_env_opt_in(monkeypatch) -> None:
    clear_tools()
    monkeypatch.delenv(PROCESS_VERIFICATION_ENV, raising=False)

    result = verify_shell("echo hello")

    assert result.ok is False
    assert "process checks require" in result.reason


def test_process_checks_allowed_when_shell_tool_loaded(monkeypatch) -> None:
    clear_tools()
    monkeypatch.delenv(PROCESS_VERIFICATION_ENV, raising=False)
    set_tools([ToolSpec(name="shell", desc="Run shell commands")])

    command = f"{sys.executable} -c \"print('ready')\""
    result = verify_shell(command, expected="ready")

    assert result.ok is True
    assert result.actual == "ready"


def test_verify_shell_expected_stdout_mismatch(monkeypatch) -> None:
    monkeypatch.setenv(PROCESS_VERIFICATION_ENV, "1")
    command = f"{sys.executable} -c \"print('ready')\""

    result = verify_shell(command, expected="missing")

    assert result.ok is False
    assert result.reason == "expected text was not present in stdout"
    assert "ready" in (result.actual or "")


def test_verify_pytest_passes_and_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(PROCESS_VERIFICATION_ENV, "1")
    passing = tmp_path / "test_passing.py"
    failing = tmp_path / "test_failing.py"
    passing.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    failing.write_text("def test_bad():\n    assert False\n", encoding="utf-8")

    assert verify_test_passes(str(passing)).ok is True
    assert verify_test_fails(str(failing)).ok is True
    assert verify_test_passes(str(failing)).ok is False
    assert verify_test_fails(str(passing)).ok is False


def test_verify_claim_dispatches_alias_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GPTME_VERIFY_ALIAS_ENV", "yes")
    path = tmp_path / "sample.txt"
    path.write_text("needle\n", encoding="utf-8")

    assert verify_claim("contains", target=str(path), expected="needle").ok is True
    assert verify_claim("env_var", target="GPTME_VERIFY_ALIAS_ENV").ok is True
    assert verify_claim("unknown", target=str(path)).ok is False


def test_execute_verify_claim_returns_json(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("needle\n", encoding="utf-8")

    message = execute_verify_claim(
        None,
        None,
        {"type": "file_exists", "target": str(path)},
    )
    payload = json.loads(message.content)

    assert message.role == "system"
    assert payload["ok"] is True
    assert payload["claim_type"] == "file_exists"


def test_execute_verify_claim_parses_key_value_block(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("needle\n", encoding="utf-8")

    message = execute_verify_claim(
        f"type: contains\ntarget: {path}\npattern: needle",
        None,
        None,
    )
    payload = json.loads(message.content)

    assert payload["ok"] is True
    assert payload["claim_type"] == "contains"


def test_run_process_timeout_cleanup_is_bounded(monkeypatch) -> None:
    """Timeout cleanup must never fall through to an unbounded communicate().

    Regression guard for the Greptile P1: when the first bounded communicate()
    times out (a surviving descendant retains the pipe write-ends, so EOF never
    arrives), the cleanup must not call communicate() with no timeout, which
    would block the caller indefinitely. It should bound every wait and close
    the pipes instead.
    """
    import subprocess
    from types import SimpleNamespace

    from gptme.tools import verify as verify_mod

    monkeypatch.setenv(PROCESS_VERIFICATION_ENV, "1")

    calls: list[tuple] = []

    class FakeProc:
        pid = 12345
        returncode = None
        stdout = SimpleNamespace(close=lambda: calls.append(("close_stdout", None)))
        stderr = SimpleNamespace(close=lambda: calls.append(("close_stderr", None)))

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        def kill(self):
            calls.append(("kill", None))

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    def fake_popen(*args, **kwargs):
        calls.append(("popen", None))
        return FakeProc()

    monkeypatch.setattr(verify_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        verify_mod.subprocess, "TimeoutExpired", subprocess.TimeoutExpired
    )

    result = verify_mod.verify_shell("sleep 10", timeout=0.1)

    assert result.ok is False
    assert "timed out" in result.reason
    # Every communicate()/wait() on the proc must carry an explicit timeout.
    for name, arg in calls:
        if name in ("communicate", "wait"):
            assert arg is not None, f"unbounded {name}() with no timeout in {calls}"
    # The unbounded fallback communicate() must never be reached.
    assert not any(name == "communicate" and arg is None for name, arg in calls)
    # Cleanup should close the pipe streams once waits are exhausted.
    assert ("close_stdout", None) in calls
    assert ("close_stderr", None) in calls
    # (monkeypatch restores the module-level Popen automatically after the test.)
