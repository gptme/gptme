"""Integration tests for TOOL_CONFIRM guardrail hooks (gptme#3598).

Validates that a third-party plugin can register a TOOL_CONFIRM hook that
blocks tool execution — including commands that the built-in allowlist would
otherwise auto-approve — and that the block is returned as a system message.
"""

import pytest

from gptme.hooks import HookType, get_hooks, register_hook, unregister_hook
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
            # Check preview first: for bg sequences it contains the full command
            # context including preceding lines; tool_use.content only holds bg_cmd.
            cmd = preview or tool_use.content or ""
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

        This test simulates headless mode by first registering cli_confirm
        (normal interactive mode), then removing it to simulate --no-confirm,
        and proving the guardrail still fires despite cli_confirm being absent.
        """
        from gptme.hooks.cli_confirm import register as register_cli_confirm
        from gptme.hooks.server_confirm import register as register_server_confirm

        sentinel = tmp_path / "headless_test.txt"

        # Save the pre-test hook set so the finally block can restore exactly it.
        _pre_test_hooks = {h.name for h in get_hooks(HookType.TOOL_CONFIRM)}
        cli_confirm_was_registered = "cli_confirm" in _pre_test_hooks
        server_confirm_was_registered = "server_confirm" in _pre_test_hooks

        # Simulate normal interactive mode: register the built-in confirm hook.
        register_cli_confirm()

        try:
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
        finally:
            # Restore hooks to exactly their pre-test state.
            if cli_confirm_was_registered:
                register_cli_confirm()
            if server_confirm_was_registered:
                register_server_confirm()

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

    def test_preceding_cmds_visible_to_hook_chain(self, tmp_path):
        """Hooks see preceding commands in a multi-line bg sequence.

        A guardrail that blocks `secret_file` must fire even when that command
        precedes an innocuous `bg ls` — previously the hook only saw `ls` and
        would approve the full sequence, letting the preceding command run.
        """
        sentinel = tmp_path / "preceded_bg_test.txt"

        # Guardrail that blocks any command containing "secret_file"
        register_hook(
            "test.guardrail",
            HookType.TOOL_CONFIRM,
            self._make_guardrail("secret_file"),
            priority=200,
        )

        # The dangerous command precedes an innocent bg payload.
        # Without the fix, hooks only saw the bg_cmd ("touch ...") and approved;
        # "secret_file" only appears in the preceding line which was invisible.
        # Use `touch` (not `ls`) as the bg payload so an unblocked run creates the
        # sentinel, making the assertion non-vacuous.
        tool_use = ToolUse(
            tool="shell",
            args=[],
            content=f"cat secret_file\nbg touch {sentinel}",
        )
        msgs = list(tool_use.execute())

        assert not sentinel.exists(), (
            "Guardrail must see preceding commands (via preview) and block the whole "
            f"sequence; messages: {[m.content for m in msgs]}"
        )


class TestAllowEdit:
    """Tests for allow_edit parameter in execute_with_confirmation."""

    def test_allow_edit_false_ignores_user_edits(self, tmp_path):
        """When allow_edit=False, an EDIT result aborts execution entirely.

        Regression: allow_edit was accepted by execute_with_confirmation but
        never forwarded, so user edits on bg sequences with surrounding commands
        were silently applied to the bg payload even when they should be ignored.

        For bg commands with preceding/remaining commands, _bg_execute_fn only
        applies the edited content to the bg portion (c parameter); edits to
        the surrounding commands shown in the preview would be silently ignored,
        causing execution to diverge from what the user approved.  Setting
        allow_edit=False causes execute_with_confirmation to abort execution
        (rather than run either the original or the edited content) when the
        user attempts an edit.
        """
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmAction, ConfirmationResult
        from gptme.util.ask_execute import execute_with_confirmation

        original_code = "ls"
        edited_code = "rm -rf /"

        # Track what content execute_fn receives
        received = []

        def execute_fn(content, path):
            received.append(content)
            return iter([])

        # Hook returns EDIT with dangerous substitution
        def mock_get_confirmation(**kwargs):
            return ConfirmationResult(
                action=ConfirmAction.EDIT,
                edited_content=edited_code,
            )

        with patch(
            "gptme.hooks.get_confirmation",
            side_effect=mock_get_confirmation,
        ):
            messages = list(
                execute_with_confirmation(
                    original_code,
                    args=[],
                    kwargs={},
                    execute_fn=execute_fn,
                    get_path_fn=lambda code, args, kwargs: None,
                    allow_edit=False,
                )
            )

        # Execution must be aborted — execute_fn must not be called at all
        assert received == [], (
            f"allow_edit=False must abort execution when an EDIT is returned; "
            f"execute_fn received {received!r} but should have received nothing"
        )
        # The abort must surface as a system message, not silent failure
        assert any(
            "not supported" in (getattr(m, "content", "") or "").lower()
            or "aborted" in (getattr(m, "content", "") or "").lower()
            for m in messages
        ), f"Expected an abort system message but got: {messages!r}"

    def test_allow_edit_true_reconfirms_user_edits(self, tmp_path):
        """Edited content is routed through TOOL_CONFIRM before execution."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.util.ask_execute import execute_with_confirmation

        original_code = "ls /tmp"
        edited_code = "ls /home"
        received = []

        def execute_fn(content, path):
            received.append(content)
            return iter([])

        with patch(
            "gptme.hooks.get_confirmation",
            side_effect=[
                ConfirmationResult.edit(edited_code),
                ConfirmationResult.confirm(),
            ],
        ) as mock_get_confirmation:
            list(
                execute_with_confirmation(
                    original_code,
                    args=[],
                    kwargs={},
                    execute_fn=execute_fn,
                    get_path_fn=lambda code, args, kwargs: None,
                    allow_edit=True,
                )
            )

        assert received == [edited_code]
        assert mock_get_confirmation.call_count == 2
        assert mock_get_confirmation.call_args_list[1].kwargs["preview"] == edited_code

    def test_empty_edit_aborts_without_execution(self):
        """Clearing editable content cancels instead of executing an empty value."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.util.ask_execute import execute_with_confirmation

        received = []

        def execute_fn(content, path):
            received.append(content)
            return iter([])

        with patch(
            "gptme.hooks.get_confirmation",
            return_value=ConfirmationResult.edit(""),
        ) as mock_get_confirmation:
            messages = list(
                execute_with_confirmation(
                    "ls /tmp",
                    args=[],
                    kwargs={},
                    execute_fn=execute_fn,
                    get_path_fn=lambda code, args, kwargs: None,
                    allow_edit=True,
                )
            )

        assert received == []
        assert mock_get_confirmation.call_count == 1
        assert any("no content" in message.content.lower() for message in messages)

    def test_identical_edit_does_not_reconfirm(self):
        """An edit that leaves content unchanged executes without another prompt."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.util.ask_execute import execute_with_confirmation

        code = "ls /tmp"
        received = []

        def execute_fn(content, path):
            received.append(content)
            return iter([])

        with patch(
            "gptme.hooks.get_confirmation",
            return_value=ConfirmationResult.edit(code),
        ) as mock_get_confirmation:
            messages = list(
                execute_with_confirmation(
                    code,
                    args=[],
                    kwargs={},
                    execute_fn=execute_fn,
                    get_path_fn=lambda code, args, kwargs: None,
                    allow_edit=True,
                )
            )

        assert received == [code]
        assert mock_get_confirmation.call_count == 1
        assert not any(
            "content was edited" in getattr(message, "content", "")
            for message in messages
        )

    def test_standalone_bg_edit_reconfirms_edited_preview(self):
        """A standalone bg edit must show the exact edited command on reconfirm."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.tools.base import ToolUse

        edited_cmd = "printf edited"
        tool_use = ToolUse(tool="shell", args=[], content="bg printf original")

        with (
            patch(
                "gptme.hooks.get_confirmation",
                side_effect=[
                    ConfirmationResult.edit(edited_cmd),
                    ConfirmationResult.skip("test stop"),
                ],
            ) as mock_get_confirmation,
            patch("gptme.tools.shell.execute_bg_command") as mock_execute,
        ):
            list(tool_use.execute())

        assert mock_get_confirmation.call_count == 2
        assert mock_get_confirmation.call_args_list[1].kwargs["preview"] == (
            f"bg {edited_cmd}"
        )
        mock_execute.assert_not_called()

    def test_guardrail_can_deny_edited_content(self):
        """A dangerous edit must not execute after the original was approved."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.util.ask_execute import execute_with_confirmation

        received = []

        def execute_fn(content, path):
            received.append(content)
            return iter([])

        with patch(
            "gptme.hooks.get_confirmation",
            side_effect=[
                ConfirmationResult.edit("rm -rf /"),
                ConfirmationResult.skip("Blocked edited command"),
            ],
        ):
            messages = list(
                execute_with_confirmation(
                    "ls",
                    args=[],
                    kwargs={},
                    execute_fn=execute_fn,
                    get_path_fn=lambda code, args, kwargs: None,
                    allow_edit=True,
                )
            )

        assert received == []
        assert any("Blocked edited command" in message.content for message in messages)

    def test_foreground_shell_denylist_rechecks_edited_command(self):
        """A confirmed edit must still pass the shell denylist before execution."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.tools.base import ToolUse

        tool_use = ToolUse(tool="shell", args=[], content="printf safe")
        with (
            patch(
                "gptme.hooks.get_confirmation",
                side_effect=[
                    ConfirmationResult.edit("rm -rf /"),
                    ConfirmationResult.confirm(),
                ],
            ),
            patch("gptme.tools.shell.execute_shell_impl") as mock_execute,
        ):
            messages = list(tool_use.execute())

        mock_execute.assert_not_called()
        assert any("Command denied" in message.content for message in messages)

    def test_background_shell_denylist_rechecks_edited_command(self):
        """A confirmed bg edit must still pass the shell denylist before execution."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.tools.base import ToolUse

        tool_use = ToolUse(tool="shell", args=[], content="bg printf safe")
        with (
            patch(
                "gptme.hooks.get_confirmation",
                side_effect=[
                    ConfirmationResult.edit("rm -rf /"),
                    ConfirmationResult.confirm(),
                ],
            ),
            patch("gptme.tools.shell.execute_bg_command") as mock_execute,
        ):
            messages = list(tool_use.execute())

        mock_execute.assert_not_called()
        assert any("Command denied" in message.content for message in messages)
