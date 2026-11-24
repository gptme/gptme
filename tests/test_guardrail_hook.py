"""Integration tests for TOOL_CONFIRM guardrail hooks (gptme#3598).

Validates that a third-party plugin can register a TOOL_CONFIRM hook that
blocks tool execution — including commands that the built-in allowlist would
otherwise auto-approve — and that the block is returned as a system message.
"""

import pytest

from gptme.hooks import HookType, get_hooks, register_hook, unregister_hook
from gptme.hooks.confirm import ConfirmationResult
from gptme.message import Message
from gptme.tools.base import ToolSpec, ToolUse


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

    def test_background_flag_edit_reconfirms_edited_preview(self):
        """A background-tool-call edit must show the exact edited command."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.tools.base import ToolUse

        edited_cmd = "printf edited"
        tool_use = ToolUse(
            tool="shell",
            args=[],
            content=None,
            kwargs={"command": "printf original", "background": "true"},
        )

        with (
            patch(
                "gptme.hooks.get_confirmation",
                side_effect=[
                    ConfirmationResult.edit(edited_cmd),
                    ConfirmationResult.skip("test stop"),
                ],
            ) as mock_get_confirmation,
            patch("gptme.tools.shell.start_background_job") as mock_execute,
        ):
            list(tool_use.execute())

        assert mock_get_confirmation.call_count == 2
        assert mock_get_confirmation.call_args_list[1].kwargs["preview"] == edited_cmd
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
        assert any(
            "Command denied" in message.content
            and "Nothing was executed" in message.content
            for message in messages
        )

    def test_background_shell_denylist_rechecks_edited_command(self):
        """A confirmed background edit must pass the denylist before execution."""
        from unittest.mock import patch

        from gptme.hooks.confirm import ConfirmationResult
        from gptme.tools.base import ToolUse

        tool_use = ToolUse(
            tool="shell",
            args=[],
            content=None,
            kwargs={"command": "printf safe", "background": "true"},
        )
        with (
            patch(
                "gptme.hooks.get_confirmation",
                side_effect=[
                    ConfirmationResult.edit("rm -rf /"),
                    ConfirmationResult.confirm(),
                ],
            ),
            patch("gptme.tools.shell.start_background_job") as mock_execute,
        ):
            messages = list(tool_use.execute())

        mock_execute.assert_not_called()
        assert any(
            "Command denied" in message.content
            and "Nothing was executed" in message.content
            for message in messages
        )


class TestPreExecutionSeam:
    """The central TOOL_EXECUTE_PRE seam is the policy seam for unarmed tools.

    ``read``, ``gh``, ``browser`` and ``convert`` never call
    ``execute_with_confirmation()``, so TOOL_CONFIRM cannot police them. A
    pre-hook denial fires for every tool before any confirmation, so it both
    reaches them and never consumes a user's decision.
    """

    @staticmethod
    def _register_guardrails(monkeypatch, mode: str) -> None:
        from gptme.hooks.guardrails import register

        monkeypatch.setenv("GPTME_GUARDRAILS", mode)
        register()

    @staticmethod
    def _unregister_guardrails() -> None:
        unregister_hook("guardrails", HookType.TOOL_EXECUTE_PRE)
        unregister_hook("guardrails", HookType.TOOL_CONFIRM)

    def test_read_secret_path_denied_end_to_end(self, tmp_path, monkeypatch):
        """A real ``read`` call never runs on a secret path."""
        from gptme.tools import load_tool

        load_tool("read")  # not in the default tool set
        secret = tmp_path / ".env"
        secret.write_text("TOKEN=unique-synthetic-value\n")
        self._register_guardrails(monkeypatch, "enforce")
        try:
            msgs = list(
                ToolUse(tool="read", args=[str(secret)], content=None).execute()
            )
        finally:
            self._unregister_guardrails()

        contents = "\n".join(m.content for m in msgs)
        assert "unique-synthetic-value" not in contents
        assert "[policy-block]" in contents
        assert "Secret path access blocked" in contents

    def test_read_safe_path_still_reads(self, tmp_path, monkeypatch):
        from gptme.tools import load_tool

        load_tool("read")
        notes = tmp_path / "notes.txt"
        notes.write_text("ordinary notes\n")
        self._register_guardrails(monkeypatch, "enforce")
        try:
            msgs = list(ToolUse(tool="read", args=[str(notes)], content=None).execute())
        finally:
            self._unregister_guardrails()

        assert any("ordinary notes" in m.content for m in msgs)

    def test_seam_denial_skips_the_confirmation_chain(self, tmp_path, monkeypatch):
        """Policy blocks without becoming the confirmer (the Tracekit root cause).

        A denial at the seam fires *before* TOOL_CONFIRM, so no prompt is
        produced and no confirmation hook gets to answer for the user.
        """
        called: list[str] = []

        def spy_confirm(tool_use, preview=None, workspace=None):
            called.append(tool_use.tool)
            return

        def deny_all(data):
            data.deny("policy decision at the seam")
            return []

        register_hook(
            "test.spy_confirm", HookType.TOOL_CONFIRM, spy_confirm, priority=1000
        )
        register_hook("test.deny", HookType.TOOL_EXECUTE_PRE, deny_all)
        try:
            msgs = list(
                ToolUse(tool="shell", args=[], content="touch never-created").execute()
            )
        finally:
            unregister_hook("test.deny", HookType.TOOL_EXECUTE_PRE)
            unregister_hook("test.spy_confirm", HookType.TOOL_CONFIRM)

        assert called == [], "a seam denial must not reach the consent chain"
        contents = "\n".join(m.content for m in msgs)
        assert "policy decision at the seam" in contents

    @pytest.mark.parametrize("tool_name", ["gh", "browser", "convert"])
    def test_deny_covers_tools_without_confirmation(self, tool_name, monkeypatch):
        """Tools that never confirm are still stoppable, with no per-tool wiring."""
        ran: list[str] = []

        def execute(code, args, kwargs):
            ran.append(tool_name)
            yield Message("system", "ran")

        spec = ToolSpec(name=tool_name, desc="test", execute=execute)

        def deny_all(data):
            data.deny("covered by the central seam")
            return []

        monkeypatch.setattr(
            "gptme.tools.get_tool",
            lambda name: spec if name == tool_name else None,
        )
        register_hook("test.deny", HookType.TOOL_EXECUTE_PRE, deny_all)
        try:
            msgs = list(ToolUse(tool=tool_name, args=[], content="payload").execute())
        finally:
            unregister_hook("test.deny", HookType.TOOL_EXECUTE_PRE)

        assert ran == [], f"a denied {tool_name} call must not execute"
        assert any("covered by the central seam" in m.content for m in msgs)


class TestPatchManySecretTargets:
    """``patch_many`` writes are policed through the public targets accessor."""

    PATCH = "<<<<<<< ORIGINAL\nold\n=======\nnew\n>>>>>>> UPDATED"

    def _call(self, benign, secret) -> ToolUse:
        import json

        patches = [
            {"path": str(benign), "patch": self.PATCH},
            {"path": str(secret), "patch": self.PATCH},
        ]
        return ToolUse(
            tool="patch_many",
            args=None,
            content=None,
            kwargs={"patches": json.dumps(patches)},
        )

    @pytest.mark.parametrize("mode", ["enforce", "shadow"])
    def test_secret_target_denied_only_in_enforce(self, tmp_path, monkeypatch, mode):
        from gptme.hooks.guardrails import register

        benign = tmp_path / "notes.txt"
        secret = tmp_path / ".env"
        benign.write_text("old\n")
        secret.write_text("old\n")

        monkeypatch.setenv("GPTME_GUARDRAILS", mode)
        register()
        try:
            msgs = list(self._call(benign, secret).execute())
        finally:
            unregister_hook("guardrails", HookType.TOOL_EXECUTE_PRE)
            unregister_hook("guardrails", HookType.TOOL_CONFIRM)

        if mode == "enforce":
            contents = "\n".join(m.content for m in msgs)
            assert "Secret path access blocked" in contents
            # Atomic denial: the benign file is untouched too.
            assert benign.read_text() == "old\n"
            assert secret.read_text() == "old\n"
        else:
            assert secret.read_text() == "new\n"
            assert benign.read_text() == "new\n"


class TestTmuxShellText:
    """``tmux`` new-session/send-keys text gets the shell tool's rules."""

    @staticmethod
    def _run(monkeypatch, mode: str, content: str) -> str:
        from gptme.hooks.guardrails import register

        monkeypatch.setenv("GPTME_GUARDRAILS", mode)
        register()
        # Record instead of spawning/querying tmux: only the seam decision is under test.
        monkeypatch.setattr("gptme.tools.tmux.get_tmux_pane_cwd", lambda _: None)
        ran: list[str] = []

        def fake_execute(code, args, kwargs):
            ran.append(code or "")
            yield Message("system", "ran")

        spec = ToolSpec(name="tmux", desc="test", execute=fake_execute)
        monkeypatch.setattr(
            "gptme.tools.get_tool", lambda name: spec if name == "tmux" else None
        )
        try:
            msgs = list(ToolUse(tool="tmux", args=[], content=content).execute())
        finally:
            unregister_hook("guardrails", HookType.TOOL_EXECUTE_PRE)
            unregister_hook("guardrails", HookType.TOOL_CONFIRM)
        return "ran" if ran else "\n".join(m.content for m in msgs)

    @pytest.mark.parametrize(
        "content",
        [
            "new-session cat ~/.ssh/id_rsa",
            "send-keys gptme_1 'cat ~/.ssh/id_rsa' Enter",
            "send-keys gptme_1\tcat ~/.ssh/id_rsa",
            "send-keys gptme_1 'printenv' Enter",
            "send-keys gptme_1 'rm -rf /' Enter",
            "send-keys gptme_1 'print' 'env' C-m",
            "new-session rm -rf /",
        ],
    )
    def test_enforce_denies(self, monkeypatch, content):
        out = self._run(monkeypatch, "enforce", content)
        assert out != "ran"
        assert "blocked" in out.lower()

    @pytest.mark.parametrize(
        "content",
        ["new-session python3 -m http.server", "send-keys gptme_1 'ls -la' Enter"],
    )
    def test_enforce_allows_ordinary(self, monkeypatch, content):
        assert self._run(monkeypatch, "enforce", content) == "ran"

    def test_shadow_does_not_block(self, monkeypatch):
        assert (
            self._run(monkeypatch, "shadow", "new-session cat ~/.ssh/id_rsa") == "ran"
        )


class TestStructuredConfirmationGuardrails:
    PATCH = "<<<<<<< ORIGINAL\nold\n=======\nnew\n>>>>>>> UPDATED"

    @pytest.mark.parametrize("mode", ["enforce", "shadow", "off"])
    def test_confirmation_checks_patch_many_payload(self, tmp_path, monkeypatch, mode):
        from gptme.hooks.confirm import ConfirmAction
        from gptme.hooks.guardrails import guardrail_hook

        monkeypatch.setenv("GPTME_GUARDRAILS", mode)
        safe = ToolUse("patch_many", [str(tmp_path / "notes.txt")], self.PATCH)
        # Reconfirmation must inspect the edited preview, not the original call.
        preview = f"=== PATH: {tmp_path / '.env'} ===\n{self.PATCH}"
        result = guardrail_hook(safe, preview=preview)
        if mode == "enforce":
            assert result is not None and result.action == ConfirmAction.SKIP
        else:
            assert result is None

    @pytest.mark.parametrize("form", ["kwargs", "headers", "positional"])
    @pytest.mark.parametrize("secret_alias", [True, False])
    def test_symlink_names_and_destinations(
        self, tmp_path, monkeypatch, form, secret_alias
    ):
        import json

        from gptme.hooks.guardrails import register

        target = tmp_path / ("runtime" if secret_alias else ".env")
        alias = tmp_path / (".env" if secret_alias else "runtime")
        target.write_text("old\n")
        alias.symlink_to(target)
        if form == "kwargs":
            use = ToolUse(
                "patch_many",
                None,
                None,
                {"patches": json.dumps([{"path": str(alias), "patch": self.PATCH}])},
            )
        elif form == "headers":
            use = ToolUse("patch_many", [], f"=== PATH: {alias} ===\n{self.PATCH}")
        else:
            use = ToolUse("patch_many", [str(alias)], self.PATCH)
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        register()
        try:
            messages = list(use.execute())
        finally:
            unregister_hook("guardrails", HookType.TOOL_EXECUTE_PRE)
            unregister_hook("guardrails", HookType.TOOL_CONFIRM)
        assert target.read_text() == "old\n"
        assert any("Secret path access blocked" in m.content for m in messages)

    def test_edited_target_does_not_write(self, tmp_path, monkeypatch):
        from gptme.hooks.guardrails import register

        safe, secret = tmp_path / "notes.txt", tmp_path / ".env"
        safe.write_text("old\n")
        secret.write_text("old\n")
        monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
        register()

        def edit_target(tool_use, preview=None, workspace=None):
            if preview and str(secret) in preview:
                return ConfirmationResult.confirm()
            return ConfirmationResult.edit(f"=== PATH: {secret} ===\n{self.PATCH}")

        register_hook(
            "test.guardrail", HookType.TOOL_CONFIRM, edit_target, priority=100
        )
        try:
            messages = list(ToolUse("patch_many", [str(safe)], self.PATCH).execute())
        finally:
            unregister_hook("guardrails", HookType.TOOL_EXECUTE_PRE)
            unregister_hook("guardrails", HookType.TOOL_CONFIRM)
        assert secret.read_text() == "old\n"
        assert safe.read_text() == "old\n"
        assert any("Secret path access blocked" in m.content for m in messages)


class TestFileToolSecretPaths:
    """Every file tool is checked in enforce mode, before it runs."""

    PATCH = "<<<<<<< ORIGINAL\nold\n=======\nnew\n>>>>>>> UPDATED"

    @staticmethod
    def _execute(monkeypatch, use: ToolUse, mode: str = "enforce") -> str:
        from gptme.hooks.guardrails import register

        monkeypatch.setenv("GPTME_GUARDRAILS", mode)
        register()
        try:
            msgs = list(use.execute())
        finally:
            unregister_hook("guardrails", HookType.TOOL_EXECUTE_PRE)
            unregister_hook("guardrails", HookType.TOOL_CONFIRM)
        return "\n".join(m.content for m in msgs)

    def test_save_secret_path_denied(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / ".ssh" / "authorized_keys"
        out = self._execute(monkeypatch, ToolUse("save", [str(target)], "key\n"))
        assert "Secret path access blocked" in out
        assert not target.exists()

    def test_save_native_kwargs_denied(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        use = ToolUse("save", None, None, {"path": str(target), "content": "x\n"})
        out = self._execute(monkeypatch, use)
        assert "Secret path access blocked" in out
        assert not target.exists()

    def test_append_secret_path_denied(self, tmp_path, monkeypatch):
        target = tmp_path / ".env"
        target.write_text("A=1\n")
        out = self._execute(monkeypatch, ToolUse("append", [str(target)], "B=2\n"))
        assert "Secret path access blocked" in out
        assert target.read_text() == "A=1\n"

    def test_patch_denied_before_reading(self, tmp_path, monkeypatch):
        # Hit and miss must give the same answer, so the file is never read.
        target = tmp_path / ".env"
        target.write_text("old\n")
        out = self._execute(monkeypatch, ToolUse("patch", [str(target)], self.PATCH))
        assert "Secret path access blocked" in out
        assert target.read_text() == "old\n"
        missing = self._execute(
            monkeypatch, ToolUse("patch", [str(tmp_path / "x.pem")], self.PATCH)
        )
        assert "Secret path access blocked" in missing

    def test_morph_denied_before_running(self, tmp_path, monkeypatch):
        ran: list[str] = []

        def fake_execute(code, args, kwargs):
            ran.append(code or "")
            yield Message("system", "ran")

        spec = ToolSpec(name="morph", desc="test", execute=fake_execute)
        monkeypatch.setattr(
            "gptme.tools.get_tool", lambda name: spec if name == "morph" else None
        )
        out = self._execute(
            monkeypatch, ToolUse("morph", [str(tmp_path / ".env")], "edit")
        )
        assert not ran
        assert "Secret path access blocked" in out

    def test_case_variant_of_secret_dir_denied(self, tmp_path, monkeypatch):
        # Case-insensitive filesystems (macOS, Windows) map .SSH to .ssh.
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / ".SSH" / "config"
        out = self._execute(monkeypatch, ToolUse("save", [str(target)], "Host *\n"))
        assert "Secret path access blocked" in out
        assert not target.exists()

    def test_read_through_symlink_denied(self, tmp_path, monkeypatch):
        from gptme.tools import load_tool

        load_tool("read")  # not in the default tool set
        monkeypatch.setenv("HOME", str(tmp_path))
        secret = tmp_path / ".ssh" / "id_rsa"
        secret.parent.mkdir()
        secret.write_text("PRIVATE KEY\n")
        workspace = tmp_path / "work"
        workspace.mkdir()
        (workspace / "notes.txt").symlink_to(secret)
        monkeypatch.chdir(workspace)
        out = self._execute(
            monkeypatch, ToolUse("read", [str(workspace / "notes.txt")], None)
        )
        assert "PRIVATE KEY" not in out
        assert "Secret path access blocked" in out

    def test_ordinary_save_allowed(self, tmp_path, monkeypatch):
        target = tmp_path / "notes.txt"
        self._execute(monkeypatch, ToolUse("save", [str(target)], "hi\n"))
        assert target.read_text() == "hi\n"
