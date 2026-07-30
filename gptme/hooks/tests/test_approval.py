"""Tests for CLODEx Phase 3a: approval gate and registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from gptme.hooks.approval import (
    OP_DESTRUCTIVE,
    OP_MODIFYING,
    OP_RISKY,
    OP_SAFE,
    ApprovalRegistry,
    _intent_hash,
    classify_tool,
)

# ---------------------------------------------------------------------------
# classify_tool
# ---------------------------------------------------------------------------


class TestClassifyTool:
    def test_read_is_safe(self):
        assert classify_tool("read", {"path": "/tmp/foo.txt"}) == OP_SAFE

    def test_browser_is_safe(self):
        assert classify_tool("browser", {"url": "https://example.com"}) == OP_SAFE

    def test_write_is_modifying(self):
        assert (
            classify_tool("write", {"path": "foo.py", "content": "x"}) == OP_MODIFYING
        )

    def test_patch_is_modifying(self):
        assert classify_tool("patch", {"path": "a.py", "patch": "diff"}) == OP_MODIFYING

    def test_shell_plain_is_modifying(self):
        assert classify_tool("shell", {"command": "echo hello"}) == OP_MODIFYING

    def test_shell_rm_is_destructive(self):
        assert classify_tool("shell", {"command": "rm -rf /tmp/old"}) == OP_DESTRUCTIVE

    def test_shell_rmdir_is_destructive(self):
        assert (
            classify_tool("shell", {"command": "rmdir /tmp/emptydir"}) == OP_DESTRUCTIVE
        )

    def test_shell_git_reset_hard_is_destructive(self):
        assert (
            classify_tool("shell", {"command": "git reset --hard HEAD~1"})
            == OP_DESTRUCTIVE
        )

    def test_shell_git_push_force_is_destructive(self):
        assert (
            classify_tool("shell", {"command": "git push --force origin master"})
            == OP_DESTRUCTIVE
        )

    def test_shell_git_branch_capital_d_is_destructive(self):
        assert (
            classify_tool("shell", {"command": "git branch -D stale-branch"})
            == OP_DESTRUCTIVE
        )

    def test_shell_gh_pr_merge_is_risky(self):
        assert classify_tool("shell", {"command": "gh pr merge 42"}) == OP_RISKY

    def test_shell_gh_pr_close_is_risky(self):
        assert classify_tool("shell", {"command": "gh pr close 99"}) == OP_RISKY

    def test_shell_git_push_master_is_risky(self):
        assert classify_tool("shell", {"command": "git push origin master"}) == OP_RISKY

    def test_shell_curl_delete_is_risky(self):
        assert (
            classify_tool(
                "shell", {"command": "curl -X DELETE https://api.example.com/v1/res"}
            )
            == OP_RISKY
        )

    def test_shell_curl_request_delete_is_risky(self):
        # --request DELETE is a valid alternate form of -X DELETE
        assert (
            classify_tool(
                "shell",
                {"command": "curl --request DELETE https://api.example.com/v1/res"},
            )
            == OP_RISKY
        )

    def test_shell_rm_with_tab_separator_is_destructive(self):
        # Tabs instead of spaces must not bypass classification
        assert (
            classify_tool("shell", {"command": "rm\t/tmp/sensitive"}) == OP_DESTRUCTIVE
        )

    def test_shell_backslash_escaped_rm_is_destructive(self):
        # r\m is a common shell-escape evasion; first-word unescaping must catch it
        assert (
            classify_tool("shell", {"command": r"r\m -rf /tmp/old"}) == OP_DESTRUCTIVE
        )

    def test_shell_leading_backslash_rm_is_destructive(self):
        # \rm bypasses shell aliases but is still rm; must be caught
        assert (
            classify_tool("shell", {"command": r"\rm -rf /tmp/old"}) == OP_DESTRUCTIVE
        )

    def test_shell_single_quoted_rm_is_destructive(self):
        # 'rm' -rf ... is valid shell; classifier must not miss it
        assert (
            classify_tool("shell", {"command": "'rm' -rf /tmp/old"}) == OP_DESTRUCTIVE
        )

    def test_shell_double_quoted_rm_is_destructive(self):
        # "rm" -rf ... is valid shell; classifier must not miss it
        assert (
            classify_tool("shell", {"command": '"rm" -rf /tmp/old'}) == OP_DESTRUCTIVE
        )

    def test_shell_embedded_quoted_rm_is_destructive(self):
        # r"m" is a bash word-concat evasion that resolves to rm; must be caught
        assert (
            classify_tool("shell", {"command": 'r"m" -rf /tmp/old'}) == OP_DESTRUCTIVE
        )

    def test_unknown_tool_defaults_to_modifying(self):
        assert classify_tool("ipython", {"code": "print(1)"}) == OP_MODIFYING

    def test_empty_command_is_modifying(self):
        assert classify_tool("shell", {"command": ""}) == OP_MODIFYING

    def test_none_command_is_modifying(self):
        assert classify_tool("shell", {"command": None}) == OP_MODIFYING


# ---------------------------------------------------------------------------
# _intent_hash
# ---------------------------------------------------------------------------


class TestIntentHash:
    def test_deterministic(self):
        h1 = _intent_hash("shell", {"command": "rm -rf /tmp/x"})
        h2 = _intent_hash("shell", {"command": "rm -rf /tmp/x"})
        assert h1 == h2

    def test_starts_with_sha256(self):
        h = _intent_hash("read", {"path": "foo.txt"})
        assert h.startswith("sha256:")

    def test_different_args_produce_different_hash(self):
        h1 = _intent_hash("shell", {"command": "rm /tmp/a"})
        h2 = _intent_hash("shell", {"command": "rm /tmp/b"})
        assert h1 != h2

    def test_different_tool_produces_different_hash(self):
        h1 = _intent_hash("write", {"path": "a.py"})
        h2 = _intent_hash("read", {"path": "a.py"})
        assert h1 != h2


# ---------------------------------------------------------------------------
# ApprovalRegistry
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path: Path) -> ApprovalRegistry:
    return ApprovalRegistry(tmp_path / "approvals.db")


class TestApprovalRegistry:
    def test_get_returns_none_for_unknown_hash(self, registry: ApprovalRegistry):
        assert registry.get("sha256:unknown") is None

    def test_is_approved_false_for_unknown(self, registry: ApprovalRegistry):
        assert not registry.is_approved("sha256:unknown")

    def test_approve_and_retrieve(self, registry: ApprovalRegistry):
        intent = _intent_hash("shell", {"command": "rm /tmp/old"})
        approval_id = registry.approve(
            session_id="sess-abc",
            intent_hash=intent,
            operation_class=OP_DESTRUCTIVE,
            tool="shell",
        )
        assert approval_id  # non-empty UUID

        record = registry.get(intent)
        assert record is not None
        assert record["status"] == "approved"
        assert record["intent_hash"] == intent
        assert record["tool"] == "shell"
        assert record["session_id"] == "sess-abc"

    def test_is_approved_true_after_approve(self, registry: ApprovalRegistry):
        intent = _intent_hash("shell", {"command": "git reset --hard HEAD~1"})
        registry.approve("s1", intent, OP_DESTRUCTIVE, "shell")
        assert registry.is_approved(intent)

    def test_idempotent_approve(self, registry: ApprovalRegistry):
        intent = _intent_hash("shell", {"command": "rm /tmp/x"})
        id1 = registry.approve("s1", intent, OP_DESTRUCTIVE, "shell")
        id2 = registry.approve("s1", intent, OP_DESTRUCTIVE, "shell")
        # Second approve replaces the record; both succeed
        assert id1
        assert id2
        assert registry.is_approved(intent)

    def test_list_session_returns_only_session_records(
        self, registry: ApprovalRegistry
    ):
        intent_a = _intent_hash("shell", {"command": "rm /tmp/a"})
        intent_b = _intent_hash("shell", {"command": "rm /tmp/b"})
        intent_c = _intent_hash("shell", {"command": "rm /tmp/c"})

        registry.approve("sess-1", intent_a, OP_DESTRUCTIVE, "shell")
        registry.approve("sess-1", intent_b, OP_DESTRUCTIVE, "shell")
        registry.approve("sess-2", intent_c, OP_DESTRUCTIVE, "shell")

        records_1 = registry.list_session("sess-1")
        assert len(records_1) == 2
        hashes_1 = {r["intent_hash"] for r in records_1}
        assert intent_a in hashes_1
        assert intent_b in hashes_1

        records_2 = registry.list_session("sess-2")
        assert len(records_2) == 1

    def test_approve_stores_workspace(self, registry: ApprovalRegistry, tmp_path: Path):
        ws = tmp_path / "my-workspace"
        ws.mkdir()
        intent = _intent_hash("shell", {"command": "rm ./stale"})
        registry.approve("s1", intent, OP_DESTRUCTIVE, "shell", workspace=ws)
        record = registry.get(intent)
        assert record is not None
        assert record["workspace"] == str(ws.resolve())

    def test_is_approved_false_for_different_workspace(
        self, registry: ApprovalRegistry, tmp_path: Path
    ):
        ws_a = tmp_path / "workspace-a"
        ws_b = tmp_path / "workspace-b"
        ws_a.mkdir()
        ws_b.mkdir()
        intent = _intent_hash("shell", {"command": "rm ./data"})
        registry.approve("s1", intent, OP_DESTRUCTIVE, "shell", workspace=ws_a)
        # Same relative command approved in workspace A must NOT pass for workspace B
        assert not registry.is_approved(intent, workspace=ws_b)

    def test_is_approved_true_for_same_workspace(
        self, registry: ApprovalRegistry, tmp_path: Path
    ):
        ws = tmp_path / "workspace"
        ws.mkdir()
        intent = _intent_hash("shell", {"command": "rm ./data"})
        registry.approve("s1", intent, OP_DESTRUCTIVE, "shell", workspace=ws)
        assert registry.is_approved(intent, workspace=ws)

    def test_is_approved_null_workspace_rejected_when_caller_provides_workspace(
        self, registry: ApprovalRegistry, tmp_path: Path
    ):
        # An approval stored without workspace must not pass workspace-aware lookups;
        # NULL-workspace entries would otherwise act as global pass-through tokens.
        intent = _intent_hash("shell", {"command": "rm /tmp/old"})
        registry.approve("s1", intent, OP_DESTRUCTIVE, "shell")  # no workspace → NULL
        assert not registry.is_approved(intent, workspace=tmp_path)

    def test_is_approved_no_workspace_caller_accepts_null_stored(
        self, registry: ApprovalRegistry
    ):
        # When the caller doesn't supply a workspace, NULL-stored approvals still pass
        # (backward-compat for programmatic / non-gated paths).
        intent = _intent_hash("shell", {"command": "rm /tmp/old"})
        registry.approve("s1", intent, OP_DESTRUCTIVE, "shell")
        assert registry.is_approved(intent)  # no workspace arg

    def test_db_created_on_demand(self, tmp_path: Path):
        db_path = tmp_path / "sub" / "dir" / "approvals.db"
        assert not db_path.exists()
        ApprovalRegistry(db_path)
        assert db_path.exists()

    def test_persists_across_instances(self, tmp_path: Path):
        db_path = tmp_path / "approvals.db"
        intent = _intent_hash("shell", {"command": "rm /tmp/persist"})

        reg1 = ApprovalRegistry(db_path)
        reg1.approve("s1", intent, OP_DESTRUCTIVE, "shell")

        reg2 = ApprovalRegistry(db_path)
        assert reg2.is_approved(intent)


# ---------------------------------------------------------------------------
# Manifest integration: approval_class field
# ---------------------------------------------------------------------------


class TestManifestApprovalClass:
    """Verify that manifest pre-records include approval_class when approval.py is present."""

    def test_pre_record_includes_approval_class(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from gptme.hooks import clear_hooks
        from gptme.hooks.manifest import register_manifest_hooks

        manifest_dir = tmp_path / "manifest"
        clear_hooks()
        register_manifest_hooks(manifest_dir)

        # Build a minimal ToolUse mock for a destructive shell command
        tool_use = MagicMock()
        tool_use.tool = "shell"
        tool_use.kwargs = {"command": "rm -rf /tmp/old"}
        tool_use.content = None

        from gptme.hooks import HookType, trigger_hook
        from gptme.hooks.types import ToolExecutePreData

        data = ToolExecutePreData(tool_use=tool_use)
        list(trigger_hook(HookType.TOOL_EXECUTE_PRE, data))

        # Find the written pre-record
        pre_files = list(manifest_dir.glob("*-pre.json"))
        assert pre_files, "No pre-record was written"
        import json

        record = json.loads(pre_files[0].read_text())
        assert record.get("approval_class") == OP_DESTRUCTIVE
        # intent_hash must be present so the manifest can be correlated to the
        # approval registry entry that authorized the operation
        assert record.get("intent_hash", "").startswith("sha256:")

    def test_pre_record_safe_op_class(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from gptme.hooks import clear_hooks
        from gptme.hooks.manifest import register_manifest_hooks

        manifest_dir = tmp_path / "manifest2"
        clear_hooks()
        register_manifest_hooks(manifest_dir)

        tool_use = MagicMock()
        tool_use.tool = "read"
        tool_use.kwargs = {"path": "/tmp/foo.txt"}
        tool_use.content = None

        from gptme.hooks import HookType, trigger_hook
        from gptme.hooks.types import ToolExecutePreData

        data = ToolExecutePreData(tool_use=tool_use)
        list(trigger_hook(HookType.TOOL_EXECUTE_PRE, data))

        import json

        pre_files = list(manifest_dir.glob("*-pre.json"))
        assert pre_files
        record = json.loads(pre_files[0].read_text())
        assert record.get("approval_class") == OP_SAFE


# ---------------------------------------------------------------------------
# execute_with_confirmation workspace threading
# ---------------------------------------------------------------------------


class TestExecuteWithConfirmationWorkspace:
    """Verify workspace is forwarded from execute_with_confirmation → get_confirmation."""

    def test_workspace_reaches_tool_confirm_hook(self, tmp_path: Path):
        """execute_with_confirmation(workspace=ws) must pass ws to TOOL_CONFIRM hooks."""
        from unittest.mock import MagicMock, patch

        from gptme.hooks import HookType, clear_hooks, register_hook
        from gptme.hooks.confirm import ConfirmationResult
        from gptme.util.ask_execute import execute_with_confirmation

        ws = tmp_path / "my-workspace"
        ws.mkdir()

        received: list[Path | None] = []

        def capture_hook(tool_use, preview=None, workspace=None):
            received.append(workspace)
            return ConfirmationResult.confirm()

        clear_hooks()
        register_hook(
            name="capture",
            hook_type=HookType.TOOL_CONFIRM,
            func=capture_hook,
            priority=0,
            enabled=True,
        )

        # get_confirmation() skips hooks when get_current_tool_use() returns None.
        # Provide a minimal ToolUse stub so the hook path is exercised.
        tool_use_stub = MagicMock()
        tool_use_stub.tool = "shell"
        tool_use_stub.kwargs = {"command": "rm -rf /tmp/old"}
        tool_use_stub.content = None

        def execute_fn(cmd: str, path):
            return iter([])

        with patch("gptme.tools.base.get_current_tool_use", return_value=tool_use_stub):
            list(
                execute_with_confirmation(
                    "rm -rf /tmp/old",
                    None,
                    None,
                    execute_fn=execute_fn,
                    get_path_fn=lambda code, args, kwargs: None,
                    workspace=ws,
                )
            )

        assert received, "TOOL_CONFIRM hook was never called"
        assert received[0] is not None, "workspace was None — not forwarded"
        assert received[0] == ws, f"expected {ws}, got {received[0]}"


class TestApprovalGateCwdFallback:
    """Verify _approval_gate falls back to cwd when workspace=None."""

    def test_approval_gate_uses_cwd_when_workspace_is_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When workspace=None, the gate must use cwd so is_approved is workspace-scoped."""
        from gptme.hooks import clear_hooks
        from gptme.hooks.approval import register_approval_hooks

        db = tmp_path / "approvals.db"
        # Change cwd to tmp_path so the gate uses tmp_path as effective_ws
        monkeypatch.chdir(tmp_path)

        clear_hooks()
        register_approval_hooks(
            approval_mode="interactive", db_path=db, session_id="test"
        )

        # Pre-approve the intent for tmp_path (the cwd the gate will use)
        from gptme.hooks.approval import OP_DESTRUCTIVE, ApprovalRegistry, _intent_hash

        intent = _intent_hash("shell", {"command": "rm /tmp/old"})
        reg = ApprovalRegistry(db)
        reg.approve("test", intent, OP_DESTRUCTIVE, "shell", workspace=tmp_path)

        # Now check: the gate should accept this approval when called with workspace=None
        # because effective_ws falls back to cwd == tmp_path
        from typing import cast
        from unittest.mock import MagicMock

        from gptme.hooks import HookType, get_hooks
        from gptme.hooks.confirm import ToolConfirmHook

        tool_use = MagicMock()
        tool_use.tool = "shell"
        tool_use.kwargs = {"command": "rm /tmp/old"}
        tool_use.content = None

        hooks = [
            h for h in get_hooks(HookType.TOOL_CONFIRM) if h.name == "approval.gate"
        ]
        assert hooks, "approval.gate hook not registered"
        gate_func = cast(ToolConfirmHook, hooks[0].func)

        result = gate_func(tool_use, workspace=None)
        assert result is None, (
            f"Expected gate to pass through (pre-approved for cwd), but got: {result}"
        )

    def test_approval_gate_rejects_wrong_workspace_even_with_cwd_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A cross-workspace approval must not pass when cwd differs from stored workspace."""
        from gptme.hooks import clear_hooks
        from gptme.hooks.approval import register_approval_hooks

        ws_a = tmp_path / "workspace-a"
        ws_b = tmp_path / "workspace-b"
        ws_a.mkdir()
        ws_b.mkdir()
        db = tmp_path / "approvals.db"

        # Pre-approve for ws_a
        from gptme.hooks.approval import OP_DESTRUCTIVE, ApprovalRegistry, _intent_hash

        intent = _intent_hash("shell", {"command": "rm /tmp/old"})
        reg = ApprovalRegistry(db)
        reg.approve("test", intent, OP_DESTRUCTIVE, "shell", workspace=ws_a)

        clear_hooks()
        register_approval_hooks(approval_mode="block", db_path=db, session_id="test")

        # Change cwd to ws_b — the gate falls back to ws_b which != ws_a
        monkeypatch.chdir(ws_b)

        from typing import cast
        from unittest.mock import MagicMock

        from gptme.hooks import HookType, get_hooks
        from gptme.hooks.confirm import ConfirmAction, ToolConfirmHook

        tool_use = MagicMock()
        tool_use.tool = "shell"
        tool_use.kwargs = {"command": "rm /tmp/old"}
        tool_use.content = None

        hooks = [
            h for h in get_hooks(HookType.TOOL_CONFIRM) if h.name == "approval.gate"
        ]
        gate_func = cast(ToolConfirmHook, hooks[0].func)

        result = gate_func(tool_use, workspace=None)
        assert result is not None, "Gate must block when stored workspace != cwd"
        assert result.action == ConfirmAction.SKIP
