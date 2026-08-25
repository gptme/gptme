"""Integration tests for TOOL_CONFIRM guardrail hooks (gptme#3598).

Validates that a third-party plugin can register a TOOL_CONFIRM hook that
blocks tool execution — including commands that the built-in allowlist would
otherwise auto-approve — and that the block is returned as a system message.
"""

import pytest

from gptme.hooks import HookType, register_hook, unregister_hook
from gptme.hooks.confirm import ConfirmationResult
from gptme.tools.base import ToolUse


@pytest.fixture(autouse=True)
def _clean_hooks():
    """Remove any test guardrail hook before and after each test."""
    unregister_hook("test.guardrail", HookType.TOOL_CONFIRM)
    yield
    unregister_hook("test.guardrail", HookType.TOOL_CONFIRM)


class TestToolConfirmGuardrailDeny:
    """A TOOL_CONFIRM hook at high priority can deny any shell command."""

    def _make_guardrail(self, blocked_pattern: str):
        def _hook(tool_use, preview=None, workspace=None):
            if tool_use.tool != "shell":
                return None
            cmd = tool_use.content or ""
            if blocked_pattern in cmd:
                return ConfirmationResult.skip(
                    f"Blocked by guardrail: {blocked_pattern!r} detected"
                )
            return None

        return _hook

    def test_guardrail_blocks_dangerous_command(self):
        """A guardrail hook can block a command that would otherwise execute.

        We use `curl evil.com` — not allowlisted (requires confirmation), not
        denylisted (no unconditional built-in block), but blocked by our guardrail.
        """
        register_hook(
            "test.guardrail",
            HookType.TOOL_CONFIRM,
            self._make_guardrail("evil.com"),
            priority=200,
        )

        tool_use = ToolUse(tool="shell", args=[], content="curl evil.com")
        msgs = list(tool_use.execute())

        assert any(
            m.role == "system" and "Blocked by guardrail" in m.content for m in msgs
        ), f"Expected a guardrail block message; got: {[m.content for m in msgs]}"

    def test_guardrail_blocks_allowlisted_command(self):
        """A guardrail hook at priority > 10 blocks even allowlisted commands.

        Before the fix in gptme#3598, `cat` was allowlisted and bypassed the
        TOOL_CONFIRM hook chain entirely. After the fix every shell command goes
        through execute_with_confirmation(), so guardrails can intercept any
        command including `cat ~/.ssh/id_rsa`.
        """
        from gptme.tools.shell_validation import is_allowlisted

        # `cat` itself is allowlisted — only the path check blocks it.
        # We use a benign path to prove the guardrail can intercept an otherwise
        # auto-approved command.
        cmd = "cat /tmp/innocuous.txt"
        assert is_allowlisted(cmd), f"Precondition: {cmd!r} should be allowlisted"

        register_hook(
            "test.guardrail",
            HookType.TOOL_CONFIRM,
            self._make_guardrail("innocuous"),
            priority=200,  # > shell_allowlist_hook priority (10)
        )

        tool_use = ToolUse(tool="shell", args=[], content=cmd)
        msgs = list(tool_use.execute())

        assert any(
            m.role == "system" and "Blocked by guardrail" in m.content for m in msgs
        ), (
            "A TOOL_CONFIRM guardrail must be able to block even allowlisted commands; "
            f"got: {[m.content for m in msgs]}"
        )

    def test_guardrail_none_allows_execution(self, tmp_path):
        """A guardrail returning None lets the tool run normally."""
        register_hook(
            "test.guardrail",
            HookType.TOOL_CONFIRM,
            self._make_guardrail("WILL_NOT_MATCH"),
            priority=200,
        )

        marker = tmp_path / "created.txt"
        tool_use = ToolUse(
            tool="shell",
            args=[],
            content=f"touch {marker}",
        )
        msgs = list(tool_use.execute())

        assert marker.exists(), (
            f"Command should have executed when guardrail returned None; "
            f"messages: {[m.content for m in msgs]}"
        )

    def test_guardrail_skip_does_not_execute_command(self, tmp_path):
        """When a guardrail returns skip(), the command must NOT run."""
        sentinel = tmp_path / "should_not_exist.txt"

        register_hook(
            "test.guardrail",
            HookType.TOOL_CONFIRM,
            self._make_guardrail("should_not_exist"),
            priority=200,
        )

        tool_use = ToolUse(
            tool="shell",
            args=[],
            content=f"touch {sentinel}",
        )
        msgs = list(tool_use.execute())

        assert not sentinel.exists(), (
            "Command must NOT have executed when guardrail returned skip(); "
            f"messages: {[m.content for m in msgs]}"
        )
        assert any(m.role == "system" for m in msgs), (
            "A skip result should produce a system message"
        )

    def test_no_confirm_mode_still_runs_guardrail(self, tmp_path):
        """In headless (no-confirm) mode, TOOL_CONFIRM guardrails still run.

        --no-confirm / -y removes cli_confirm and server_confirm from the hook
        chain, but independently-registered guardrails are unaffected.

        This test simulates headless mode by explicitly unregistering
        cli_confirm and server_confirm before execution, so the guardrail is
        the *only* hook in the chain — proving it fires independently.
        """
        sentinel = tmp_path / "headless_test.txt"

        # Simulate --no-confirm: remove the built-in confirmation hooks.
        unregister_hook("cli_confirm", HookType.TOOL_CONFIRM)
        unregister_hook("server_confirm", HookType.TOOL_CONFIRM)

        register_hook(
            "test.guardrail",
            HookType.TOOL_CONFIRM,
            self._make_guardrail("headless_test"),
            priority=200,
        )

        tool_use = ToolUse(
            tool="shell",
            args=[],
            content=f"touch {sentinel}",
        )
        msgs = list(tool_use.execute())

        assert not sentinel.exists(), (
            "Guardrail must block execution even without cli_confirm registered; "
            f"messages: {[m.content for m in msgs]}"
        )

    def test_bg_prefix_routes_through_hook_chain(self, tmp_path):
        """A `bg` prefix must not bypass the TOOL_CONFIRM hook chain.

        Before #3598, `bg cat ~/.ssh/id_rsa` returned before the hook chain,
        so a guardrail registered at high priority could never intercept it.
        """
        sentinel = tmp_path / "bg_hook_test.txt"

        register_hook(
            "test.guardrail",
            HookType.TOOL_CONFIRM,
            self._make_guardrail("bg_hook_test"),
            priority=200,
        )

        tool_use = ToolUse(
            tool="shell",
            args=[],
            content=f"bg touch {sentinel}",
        )
        msgs = list(tool_use.execute())

        assert not sentinel.exists(), (
            "Guardrail must intercept bg-prefixed commands via TOOL_CONFIRM hook chain; "
            f"messages: {[m.content for m in msgs]}"
        )
        assert any(
            m.role == "system" and "Blocked by guardrail" in m.content for m in msgs
        ), f"Expected guardrail block message; got: {[m.content for m in msgs]}"
