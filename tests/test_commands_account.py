"""Tests for the /account command."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

from gptme.commands.account import cmd_account
from gptme.commands.base import CommandContext, _command_registry


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


def test_account_switch_unknown():
    """/account <name> with unknown provider shows error."""
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
    assert "Unknown" in printed or "not found" in printed or "don't know" in printed
