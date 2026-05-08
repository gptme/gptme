"""Tests for the /account command."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

from gptme.chat import _redact_sensitive_commands
from gptme.commands.account import cmd_account
from gptme.commands.base import CommandContext, _command_registry
from gptme.message import Message


def _make_manager() -> MagicMock:
    manager = MagicMock()
    manager.log = MagicMock()
    manager.log.messages = []
    return manager


def test_account_command_registered():
    """The /account command is registered in the global registry."""
    assert "account" in _command_registry


def test_account_command_has_aliases():
    """/account has /creds as an alias."""
    assert "creds" in _command_registry


def test_account_list_with_no_providers():
    """/account with no available providers shows help text."""
    ctx = CommandContext(args=[], full_args="", manager=_make_manager())
    with (
        patch("gptme.commands.account._get_available_providers") as mock_providers,
        patch("builtins.print") as mock_print,
    ):
        mock_providers.return_value = []
        # Execute the command
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    # Should have printed help text about setting env vars
    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "No configured provider credentials" in printed


def test_account_list_with_providers():
    """/account lists available providers with env var source."""
    ctx = CommandContext(args=[], full_args="", manager=_make_manager())
    with (
        patch("gptme.commands.account._get_available_providers") as mock_providers,
        patch("gptme.commands.account._get_active_provider") as mock_active,
        patch("builtins.print") as mock_print,
    ):
        mock_providers.return_value = [
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
        ]
        mock_active.return_value = "anthropic"
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Available accounts" in printed
    assert "anthropic" in printed
    assert "openrouter" in printed
    assert "ANTHROPIC_API_KEY" in printed


def test_account_switch_unknown_subcommand():
    """/account <name> with unknown subcommand shows usage error."""
    ctx = CommandContext(
        args=["nonexistent"], full_args="nonexistent", manager=_make_manager()
    )
    with patch("builtins.print") as mock_print:
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Unknown subcommand" in printed


def test_account_switch_unknown_provider():
    """/account switch with an unsupported provider name shows an error."""
    ctx = CommandContext(
        args=["switch", "fakeprovider", "sk-123"],
        full_args="switch fakeprovider sk-123",
        manager=_make_manager(),
    )
    with patch("builtins.print") as mock_print:
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Unknown provider" in printed
    assert "fakeprovider" in printed


def test_account_switch_anthropic_success():
    """/account switch anthropic <key> calls reinit and prints success."""
    ctx = CommandContext(
        args=["switch", "anthropic", "sk-ant-api03-test"],
        full_args="switch anthropic sk-ant-api03-test",
        manager=_make_manager(),
    )
    with (
        patch("gptme.commands.account._switch_anthropic") as mock_switch,
        patch("builtins.print") as mock_print,
    ):
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    mock_switch.assert_called_once_with("sk-ant-api03-test")
    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Switched anthropic credentials" in printed


def test_account_switch_openai_like_success():
    """/account switch openai <key> calls reinit and prints success."""
    ctx = CommandContext(
        args=["switch", "openrouter", "or-test-key"],
        full_args="switch openrouter or-test-key",
        manager=_make_manager(),
    )
    with (
        patch("gptme.commands.account._switch_openai_like") as mock_switch,
        patch("builtins.print") as mock_print,
    ):
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    mock_switch.assert_called_once_with("openrouter", "or-test-key")
    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Switched openrouter credentials" in printed


def test_account_switch_missing_key_arg():
    """/account switch <provider> with no key shows usage error."""
    ctx = CommandContext(
        args=["switch", "anthropic"],
        full_args="switch anthropic",
        manager=_make_manager(),
    )
    with patch("builtins.print") as mock_print:
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Usage" in printed
    assert "api_key" in printed


def test_account_switch_raises_value_error():
    """/account switch that raises ValueError shows a friendly error."""
    ctx = CommandContext(
        args=["switch", "anthropic", ""],
        full_args="switch anthropic ",
        manager=_make_manager(),
    )
    with (
        patch(
            "gptme.commands.account._switch_anthropic",
            side_effect=ValueError("api_key must not be empty"),
        ),
        patch("builtins.print") as mock_print,
    ):
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Error switching" in printed
    assert "api_key must not be empty" in printed


# --- Credential redaction tests ---


def _user_msg(content: str) -> Message:
    return Message(role="user", content=content)


def test_redact_account_switch_key():
    """API key is redacted from /account switch before conversation logging."""
    msg = _user_msg("/account switch anthropic sk-ant-api03-realkey")
    redacted = _redact_sensitive_commands(msg)
    assert "sk-ant-api03-realkey" not in redacted.content
    assert "<REDACTED>" in redacted.content
    assert "anthropic" in redacted.content


def test_redact_creds_alias():
    """/creds switch (alias) is also redacted."""
    msg = _user_msg("/creds switch openai sk-openai-secret")
    redacted = _redact_sensitive_commands(msg)
    assert "sk-openai-secret" not in redacted.content
    assert "<REDACTED>" in redacted.content


def test_redact_preserves_provider():
    """Provider name is preserved after redaction."""
    msg = _user_msg("/account switch openrouter or-realkey-abc123")
    redacted = _redact_sensitive_commands(msg)
    assert "openrouter" in redacted.content
    assert "or-realkey-abc123" not in redacted.content


def test_no_redact_account_list():
    """/account list (no key) is not modified."""
    msg = _user_msg("/account")
    redacted = _redact_sensitive_commands(msg)
    assert redacted is msg  # same object, no copy needed


def test_no_redact_regular_message():
    """Regular messages are not modified."""
    msg = _user_msg("hello, what is the weather today?")
    redacted = _redact_sensitive_commands(msg)
    assert redacted is msg
