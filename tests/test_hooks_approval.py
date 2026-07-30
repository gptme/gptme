"""Tests for CLODEx Phase 3a approval gate and registry.

Covers:
- ApprovalRegistry CRUD and workspace matching
- _approval_gate hook: block / interactive-approve / pre-approved
- Workspace propagation from ToolUse.execute() to the approval gate (CLI path fix)
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from gptme.hooks.approval import (
    ApprovalRegistry,
    classify_tool,
)
from gptme.tools.base import ToolUse

if TYPE_CHECKING:
    from pathlib import Path

    from gptme.hooks.confirm import ConfirmationResult

# ---------------------------------------------------------------------------
# classify_tool
# ---------------------------------------------------------------------------


def test_classify_rm_destructive():
    assert classify_tool("shell", {"command": "rm -rf /tmp/foo"}) == "DESTRUCTIVE"


def test_classify_gh_pr_merge_risky():
    assert classify_tool("shell", {"command": "gh pr merge 42"}) == "RISKY"


def test_classify_write_modifying():
    assert classify_tool("write", {"path": "foo.py", "content": "x"}) == "MODIFYING"


def test_classify_read_safe():
    assert classify_tool("read", {"path": "README.md"}) == "SAFE"


def test_classify_git_push_force_destructive():
    assert (
        classify_tool("shell", {"command": "git push --force origin master"})
        == "DESTRUCTIVE"
    )


# ---------------------------------------------------------------------------
# ApprovalRegistry
# ---------------------------------------------------------------------------


@pytest.fixture()
def reg(tmp_path):
    db = tmp_path / "approvals.db"
    return ApprovalRegistry(db)


def test_registry_approve_and_check(reg, tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    intent = "sha256:aabbcc"
    reg.approve("sess1", intent, "DESTRUCTIVE", "shell", workspace=ws)
    assert reg.is_approved(intent, workspace=ws)


def test_registry_null_workspace_rejected_when_caller_has_workspace(reg, tmp_path):
    """A NULL-workspace approval must NOT pass when the caller has a workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    intent = "sha256:aabbcc"
    reg.approve("sess1", intent, "DESTRUCTIVE", "shell", workspace=None)
    assert not reg.is_approved(intent, workspace=ws)


def test_registry_workspace_mismatch_rejected(reg, tmp_path):
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    ws_a.mkdir()
    ws_b.mkdir()
    intent = "sha256:xxyy"
    reg.approve("sess1", intent, "RISKY", "shell", workspace=ws_a)
    assert reg.is_approved(intent, workspace=ws_a)
    assert not reg.is_approved(intent, workspace=ws_b)


def test_registry_no_workspace_caller_accepts_any_approval(reg, tmp_path):
    """When caller passes workspace=None, no workspace enforcement → accepts."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    intent = "sha256:zzww"
    reg.approve("sess1", intent, "RISKY", "shell", workspace=ws)
    assert reg.is_approved(intent, workspace=None)


# ---------------------------------------------------------------------------
# Workspace propagation: ToolUse.execute() → get_workspace_cwd() → approval gate
# ---------------------------------------------------------------------------


def test_workspace_propagated_from_tool_use_execute_to_approval_gate(tmp_path):
    """ToolUse.execute(workspace=...) must set the shell ContextVar so the
    approval gate receives the workspace instead of None.

    This is the CLI path fix: in the server path session_step.py calls
    set_workspace_cwd explicitly; in the CLI path only ToolUse.execute() has
    the workspace and must propagate it into the ContextVar.
    """
    from gptme.hooks import HookType, clear_hooks, register_hook
    from gptme.hooks.confirm import ConfirmationResult

    captured_workspace: list[Path | None] = []

    def spy_hook(
        tool_use: ToolUse,
        preview: str | None = None,
        workspace: Path | None = None,
    ) -> ConfirmationResult | None:
        captured_workspace.append(workspace)
        # Block execution so the command doesn't actually run
        return ConfirmationResult.skip("test spy")

    clear_hooks()
    register_hook(
        name="test.workspace_spy",
        hook_type=HookType.TOOL_CONFIRM,
        func=spy_hook,
        priority=100,
        enabled=True,
    )

    ws = tmp_path / "myproject"
    ws.mkdir()

    # Use a command that passes the denylist but is not allowlisted, so
    # execute_with_confirmation() is called and TOOL_CONFIRM hooks fire.
    # (rm -rf hits the denylist regex rm\s+-rf\s+/ and returns early.)
    tool_use = ToolUse(
        tool="shell", args=[], content="touch /tmp/clodex-workspace-propagation-test"
    )
    list(tool_use.execute(workspace=ws))  # drain generator; spy hook skips actual exec

    clear_hooks()

    assert len(captured_workspace) == 1, "spy hook should have fired once"
    assert captured_workspace[0] is not None, (
        "workspace must not be None when passed via ToolUse.execute()"
    )
    assert captured_workspace[0].resolve() == ws.resolve()


def test_workspace_tracks_persistent_shell_after_cd(tmp_path):
    """Approval scope follows the shell's actual cwd after a prior cd."""
    from gptme.hooks import HookType, clear_hooks, register_hook
    from gptme.hooks.confirm import ConfirmationResult
    from gptme.tools.shell import ShellSession, set_shell

    initial = tmp_path / "initial"
    target = tmp_path / "target"
    initial.mkdir()
    target.mkdir()
    shell = ShellSession(cwd=str(initial))
    shell.run(f"cd {target}")
    set_shell(shell)
    captured_workspace: list[Path | None] = []

    def spy_hook(tool_use, preview=None, workspace=None):
        captured_workspace.append(workspace)
        return ConfirmationResult.skip("test spy")

    clear_hooks()
    register_hook(
        name="test.workspace_after_cd",
        hook_type=HookType.TOOL_CONFIRM,
        func=spy_hook,
        priority=100,
        enabled=True,
    )
    try:
        tool_use = ToolUse(tool="shell", args=[], content="touch relative-file")
        with patch("gptme.tools.shell.get_shell", return_value=shell):
            list(tool_use.execute(workspace=initial))
    finally:
        clear_hooks()
        shell.close()

    assert captured_workspace == [target.resolve()]


def test_workspace_cannot_be_spoofed_by_pwd_function(tmp_path):
    """Approval scope uses the shell's real cwd even when `pwd` is overridden."""
    from gptme.hooks import HookType, clear_hooks, register_hook
    from gptme.hooks.confirm import ConfirmationResult
    from gptme.tools.shell import ShellSession, set_shell

    approved = tmp_path / "approved"
    target = tmp_path / "target"
    approved.mkdir()
    target.mkdir()
    shell = ShellSession(cwd=str(approved))
    shell.run(f"function pwd {{ printf '%s\\n' {approved}; }}; cd {target}")
    set_shell(shell)
    captured_workspace: list[Path | None] = []

    def spy_hook(tool_use, preview=None, workspace=None):
        captured_workspace.append(workspace)
        return ConfirmationResult.skip("test spy")

    clear_hooks()
    register_hook(
        name="test.workspace_spoof",
        hook_type=HookType.TOOL_CONFIRM,
        func=spy_hook,
        priority=100,
        enabled=True,
    )
    try:
        tool_use = ToolUse(tool="shell", args=[], content="touch relative-file")
        with patch("gptme.tools.shell.get_shell", return_value=shell):
            list(tool_use.execute(workspace=approved))
    finally:
        clear_hooks()
        shell.close()

    assert captured_workspace == [target.resolve()]


def test_workspace_uses_lsof_on_macos(tmp_path):
    """macOS obtains the shell cwd without relying on Linux procfs."""
    from unittest.mock import Mock

    from gptme.tools.shell import ShellSession

    shell = ShellSession.__new__(ShellSession)
    shell.process = Mock(pid=123)
    lsof_output = f"p123\nfcwd\nn{tmp_path}\n"

    with (
        patch("gptme.tools.shell.sys.platform", "darwin"),
        patch(
            "gptme.tools.shell.subprocess.check_output", return_value=lsof_output
        ) as check_output,
    ):
        assert shell.cwd() == tmp_path.resolve()

    check_output.assert_called_once_with(
        ["/usr/sbin/lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"],
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
    )


def test_workspace_contextvar_reset_after_execute(tmp_path):
    """After ToolUse.execute() completes, the workspace ContextVar is reset."""
    from gptme.hooks import HookType, clear_hooks, register_hook
    from gptme.hooks.confirm import ConfirmationResult
    from gptme.tools.shell import get_workspace_cwd

    clear_hooks()

    def skip_hook(tool_use, preview=None, workspace=None):
        return ConfirmationResult.skip("test")

    register_hook(
        name="test.skip",
        hook_type=HookType.TOOL_CONFIRM,
        func=skip_hook,
        priority=100,
        enabled=True,
    )

    # Verify ContextVar is None before execution
    assert get_workspace_cwd() is None

    ws = tmp_path / "project"
    ws.mkdir()
    tool_use = ToolUse(tool="shell", args=[], content="ls /tmp")
    list(tool_use.execute(workspace=ws))

    # ContextVar must be restored after execution
    assert get_workspace_cwd() is None, (
        "workspace ContextVar must be reset to None after ToolUse.execute()"
    )
    clear_hooks()
