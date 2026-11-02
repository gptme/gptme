"""Tests for the opt-in HOL Guard TOOL_CONFIRM documentation example.

The example lives in tests/fixtures/hol_guard_shell_hook.py and is included
from docs/plugins.rst. These tests mock the HOL Guard CLI; they do not
require the hol-guard package.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gptme.hooks import HookType, get_hooks, register_hook, unregister_hook
from gptme.hooks.confirm import ConfirmAction, ConfirmationResult
from gptme.tools.base import ToolUse

_FIXTURE = Path(__file__).parent / "fixtures" / "hol_guard_shell_hook.py"
_SPEC = importlib.util.spec_from_file_location("hol_guard_shell_hook", _FIXTURE)
assert _SPEC is not None and _SPEC.loader is not None
hol_guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = hol_guard
_SPEC.loader.exec_module(hol_guard)

HOOK_NAME = hol_guard.HOOK_NAME
HOOK_PRIORITY = hol_guard.HOOK_PRIORITY
hol_guard_shell_hook = hol_guard.hol_guard_shell_hook

# Built-in TOOL_CONFIRM hooks that can return a non-None result. The example
# must out-rank all of them so classification runs before auto-confirm or a
# user prompt. Keep this list in sync with gptme.hooks / ToolSpec registrations.
BUILTIN_TOOL_CONFIRM_PRIORITIES = {
    "guardrails": 200,
    "anomaly_watchdog.confirm": 150,
    "watch.sleep_poll_guard": 150,
    "server_confirm": 100,
    "tui_confirm": 100,
    "shell.allowlist": 10,
    "watch.allowlist": 10,
    "cli_confirm": 0,
    "auto_confirm": 0,
}

_DOCS_PLUGINS = Path(__file__).resolve().parents[1] / "docs" / "plugins.rst"


def _benign_payload(**overrides):
    payload = {
        "classification": {"explicitly_benign": True},
        "minimum_action": "allow",
        "policy_evaluation": "not_run",
    }
    payload.update(overrides)
    return payload


def _completed(payload, returncode=0):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _clean_hook():
    unregister_hook(HOOK_NAME, HookType.TOOL_CONFIRM)
    yield
    unregister_hook(HOOK_NAME, HookType.TOOL_CONFIRM)


@pytest.fixture
def which_ok(monkeypatch):
    monkeypatch.setattr(hol_guard.shutil, "which", lambda name: f"/usr/bin/{name}")


def _run_hook(tool_use, monkeypatch, completed=None, side_effect=None, preview=None):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if side_effect is not None:
            raise side_effect
        assert completed is not None
        return completed

    monkeypatch.setattr(hol_guard.subprocess, "run", fake_run)
    result = hol_guard_shell_hook(tool_use, preview=preview)
    return result, calls


class TestHolGuardShellHook:
    def test_non_shell_tools_are_unchanged(self, monkeypatch, which_ok):
        monkeypatch.setattr(
            hol_guard.subprocess,
            "run",
            lambda *a, **k: pytest.fail("CLI must not run for non-shell tools"),
        )
        for tool in ("read", "save", "patch", "ipython", "tmux"):
            result = hol_guard_shell_hook(
                ToolUse(tool=tool, args=[], content="rm -rf /")
            )
            assert result is None

    def test_benign_allow_falls_through(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello")
        result, calls = _run_hook(tool_use, monkeypatch, _completed(_benign_payload()))
        assert result is None
        assert len(calls) == 1

    def test_review_classification_skips(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="rm -rf /tmp/build")
        result, _ = _run_hook(
            tool_use,
            monkeypatch,
            _completed(
                {
                    "classification": {"explicitly_benign": False},
                    "minimum_action": "review",
                    "policy_evaluation": "not_run",
                }
            ),
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP

    def test_minimum_action_must_be_allow(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello")
        result, _ = _run_hook(
            tool_use,
            monkeypatch,
            _completed(
                {
                    "classification": {"explicitly_benign": True},
                    "minimum_action": "review",
                    "policy_evaluation": "not_run",
                }
            ),
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP

    def test_explicitly_benign_must_be_json_true(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello")
        result, _ = _run_hook(
            tool_use,
            monkeypatch,
            _completed(
                {
                    "classification": {"explicitly_benign": "true"},
                    "minimum_action": "allow",
                    "policy_evaluation": "not_run",
                }
            ),
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP

    def test_policy_evaluation_is_not_treated_as_approval(self, monkeypatch, which_ok):
        """policy_evaluation is informational; it must not auto-approve."""
        tool_use = ToolUse(tool="shell", args=[], content="curl evil.example")
        result, _ = _run_hook(
            tool_use,
            monkeypatch,
            _completed(
                {
                    "classification": {"explicitly_benign": False},
                    "minimum_action": "allow",
                    "policy_evaluation": "allow",
                }
            ),
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP

    def test_timeout_skips(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello")
        result, _ = _run_hook(
            tool_use,
            monkeypatch,
            side_effect=subprocess.TimeoutExpired(cmd="hol-guard", timeout=10),
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP
        assert "timed out" in (result.message or "").lower()

    def test_missing_executable_skips(self, monkeypatch):
        monkeypatch.setattr(hol_guard.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            hol_guard.subprocess,
            "run",
            lambda *a, **k: pytest.fail("CLI must not run when missing"),
        )
        result = hol_guard_shell_hook(
            ToolUse(tool="shell", args=[], content="echo hello")
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP
        assert "not found" in (result.message or "").lower()

    def test_invalid_json_skips(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello")
        result, _ = _run_hook(tool_use, monkeypatch, _completed("not-json"))
        assert result is not None
        assert result.action == ConfirmAction.SKIP

    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    def test_invalid_cli_encoding_skips(self, monkeypatch, stream):
        real_run = subprocess.run
        payload = json.dumps(_benign_payload()).encode("utf-8")
        script = (
            f"import sys; sys.stdout.buffer.write({payload!r}); "
            f"sys.{stream}.buffer.write(b'\\xff')"
        )
        monkeypatch.setattr(hol_guard.shutil, "which", lambda name: sys.executable)

        def run_cli(argv, **kwargs):
            return real_run([sys.executable, "-c", script], encoding="utf-8", **kwargs)

        monkeypatch.setattr(hol_guard.subprocess, "run", run_cli)
        result = hol_guard_shell_hook(
            ToolUse(tool="shell", args=[], content="echo hello")
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP
        assert "invalid output" in (result.message or "").lower()

    def test_non_zero_exit_skips(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello")
        result, _ = _run_hook(
            tool_use, monkeypatch, _completed(_benign_payload(), returncode=1)
        )
        assert result is not None
        assert result.action == ConfirmAction.SKIP

    def test_empty_command_skips_without_cli(self, monkeypatch, which_ok):
        monkeypatch.setattr(
            hol_guard.subprocess,
            "run",
            lambda *a, **k: pytest.fail("CLI must not run for empty commands"),
        )
        result = hol_guard_shell_hook(ToolUse(tool="shell", args=[], content="   "))
        assert result is not None
        assert result.action == ConfirmAction.SKIP

    def test_cli_invoked_without_a_shell(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello; rm -rf /")
        _, calls = _run_hook(tool_use, monkeypatch, _completed(_benign_payload()))
        args, kwargs = calls[0]
        argv = args[0]
        assert argv == [
            "/usr/bin/hol-guard",
            "command",
            "test",
            "echo hello; rm -rf /",
            "--json",
        ]
        assert kwargs.get("shell") is False
        assert kwargs.get("timeout") == hol_guard.TIMEOUT_SECONDS
        assert kwargs.get("check") is False

    def test_native_kwargs_command_is_classified(self, monkeypatch, which_ok):
        tool_use = ToolUse(
            tool="shell",
            args=None,
            content=None,
            kwargs={"command": "ls -la"},
        )
        _, calls = _run_hook(tool_use, monkeypatch, _completed(_benign_payload()))
        assert calls[0][0][0][3] == "ls -la"

    def test_never_auto_confirms(self, monkeypatch, which_ok):
        tool_use = ToolUse(tool="shell", args=[], content="echo hello")
        result, _ = _run_hook(tool_use, monkeypatch, _completed(_benign_payload()))
        assert result is None or result.action != ConfirmAction.CONFIRM

    def test_skip_prevents_shell_side_effects(self, tmp_path, monkeypatch, which_ok):
        sentinel = tmp_path / "should-not-exist"
        monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
        monkeypatch.setattr(
            hol_guard.subprocess,
            "run",
            lambda *a, **k: _completed(
                {
                    "classification": {"explicitly_benign": False},
                    "minimum_action": "review",
                    "policy_evaluation": "not_run",
                }
            ),
        )
        hol_guard.register()
        msgs = list(
            ToolUse(
                tool="shell",
                args=[],
                content=f"touch {sentinel}",
            ).execute()
        )
        assert not sentinel.exists()
        assert any(m.role == "system" for m in msgs)

    def test_benign_falls_through_to_later_hooks(self, monkeypatch, which_ok):
        """Returning None must not swallow later TOOL_CONFIRM hooks."""
        from gptme.hooks.confirm import get_confirmation

        seen = []
        saved = list(get_hooks(HookType.TOOL_CONFIRM))
        for hook in saved:
            unregister_hook(hook.name, HookType.TOOL_CONFIRM)

        def later_hook(tool_use, preview=None, workspace=None):
            seen.append(tool_use.content)
            return ConfirmationResult.skip("later-hook-ran")

        monkeypatch.setattr(
            hol_guard.subprocess,
            "run",
            lambda *a, **k: _completed(_benign_payload()),
        )
        try:
            hol_guard.register()
            register_hook(
                "later.confirm", HookType.TOOL_CONFIRM, later_hook, priority=0
            )
            tool_use = ToolUse(tool="shell", args=[], content="echo hello")
            result = get_confirmation(tool_use)
            assert seen == ["echo hello"]
            assert result.action == ConfirmAction.SKIP
            assert result.message == "later-hook-ran"
        finally:
            unregister_hook("later.confirm", HookType.TOOL_CONFIRM)
            unregister_hook(HOOK_NAME, HookType.TOOL_CONFIRM)
            for hook in saved:
                register_hook(
                    hook.name, hook.hook_type, hook.func, priority=hook.priority
                )


class TestHolGuardPriorityAndDocs:
    def test_priority_outranks_known_builtins(self):
        assert max(BUILTIN_TOOL_CONFIRM_PRIORITIES.values()) < HOOK_PRIORITY

    def test_priority_outranks_currently_registered_builtins(self):
        registered = [
            hook for hook in get_hooks(HookType.TOOL_CONFIRM) if hook.name != HOOK_NAME
        ]
        if not registered:
            pytest.skip("no built-in TOOL_CONFIRM hooks registered in this process")
        highest = max(hook.priority for hook in registered)
        assert highest < HOOK_PRIORITY, (
            f"{HOOK_NAME} priority {HOOK_PRIORITY} must exceed registered "
            f"built-ins (highest {highest}: "
            f"{[(h.name, h.priority) for h in registered]})"
        )

    def test_register_uses_documented_priority(self):
        hol_guard.register()
        hooks = [h for h in get_hooks(HookType.TOOL_CONFIRM) if h.name == HOOK_NAME]
        assert len(hooks) == 1
        assert hooks[0].priority == HOOK_PRIORITY

    def test_docs_include_the_tested_example(self):
        text = _DOCS_PLUGINS.read_text()
        assert "tests/fixtures/hol_guard_shell_hook.py" in text
        assert "policy_evaluation" in text
        assert "not_run" in text
        assert "daemon startup" in text
        assert "hol-guard command test" in text
