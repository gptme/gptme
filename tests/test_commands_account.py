"""Tests for the /account command."""

from collections.abc import Generator
from pathlib import Path
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
    """/account with no available providers shows setup guidance."""
    ctx = CommandContext(args=[], full_args="", manager=_make_manager())
    with (
        patch("gptme.commands.account._get_available_providers", return_value=[]),
        patch("builtins.print") as mock_print,
    ):
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "No configured provider credentials" in printed
    assert "/account setup" in printed


def test_account_list_with_providers():
    """/account lists configured providers with safe previews."""
    ctx = CommandContext(args=[], full_args="", manager=_make_manager())
    with (
        patch(
            "gptme.commands.account._get_available_providers",
            return_value=[
                ("anthropic", "credentials.toml"),
                ("openrouter", "OPENROUTER_API_KEY"),
            ],
        ),
        patch("gptme.commands.account._get_active_provider", return_value="anthropic"),
        patch(
            "gptme.commands.account._get_key_preview",
            side_effect=["sk-a...1234", "sk-o...5678"],
        ),
        patch("builtins.print") as mock_print,
    ):
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    printed = " ".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert "Configured accounts" in printed
    assert "anthropic" in printed
    assert "openrouter" in printed
    assert "credentials.toml" in printed
    assert "sk-a...1234" in printed


def test_account_setup_openrouter_dispatches():
    ctx = CommandContext(
        args=["setup", "openrouter"],
        full_args="setup openrouter",
        manager=_make_manager(),
    )
    with patch("gptme.commands.account._setup_openrouter") as mock_setup:
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    mock_setup.assert_called_once_with()


def test_account_setup_prompts_for_provider_when_missing():
    ctx = CommandContext(args=["setup"], full_args="setup", manager=_make_manager())
    with (
        patch(
            "gptme.commands.account._select_setup_provider", return_value="anthropic"
        ),
        patch("gptme.commands.account._setup_manual_provider") as mock_setup,
    ):
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    mock_setup.assert_called_once_with("anthropic", provided_api_key=None)


def test_account_setup_manual_provider_with_inline_key():
    ctx = CommandContext(
        args=["setup", "anthropic", "sk-ant-test"],
        full_args="setup anthropic sk-ant-test",
        manager=_make_manager(),
    )
    with patch("gptme.commands.account._setup_manual_provider") as mock_setup:
        result = cmd_account(ctx)
        if isinstance(result, Generator):
            list(result)

    mock_setup.assert_called_once_with("anthropic", provided_api_key="sk-ant-test")


def test_setup_openrouter_stores_key_and_sets_default_model():
    from gptme.commands.account import _setup_openrouter

    with (
        patch(
            "gptme.oauth.openrouter.authenticate", return_value="sk-or-test-12345678"
        ),
        patch("gptme.commands.account.set_stored_api_key") as mock_store,
        patch("gptme.commands.account._refresh_runtime_provider") as mock_refresh,
        patch("gptme.commands.account._set_default_model") as mock_set_model,
        patch("builtins.print"),
    ):
        _setup_openrouter()

    mock_store.assert_called_once_with("openrouter", "sk-or-test-12345678")
    mock_refresh.assert_called_once_with("openrouter", "sk-or-test-12345678")
    mock_set_model.assert_called_once_with("openrouter")


def test_setup_manual_provider_validates_and_stores():
    from gptme.commands.account import _setup_manual_provider

    with (
        patch("rich.prompt.Prompt.ask", return_value="sk-ant-test-1234"),
        patch(
            "gptme.commands.account.validate_api_key",
            return_value=(True, "Validated successfully"),
        ),
        patch(
            "gptme.commands.account.set_stored_api_key",
            return_value=Path("/tmp/credentials.toml"),
        ) as mock_store,
        patch("gptme.commands.account._refresh_runtime_provider") as mock_refresh,
        patch("gptme.commands.account._set_default_model") as mock_set_model,
        patch("builtins.print"),
    ):
        _setup_manual_provider("anthropic")

    mock_store.assert_called_once_with("anthropic", "sk-ant-test-1234")
    mock_refresh.assert_called_once_with("anthropic", "sk-ant-test-1234")
    mock_set_model.assert_called_once_with("anthropic")


def test_account_unknown_subcommand():
    """/account with unknown subcommand shows an error."""
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
    assert "Unknown" in printed
