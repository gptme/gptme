"""Onboarding must see API keys stored in credentials.toml (written by /account setup)."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from gptme.cli.onboard import _detect_providers, _run_wizard, _test_provider
from gptme.credentials import set_stored_api_key
from gptme.llm import list_available_providers
from gptme.llm.validate import ApiKeyValidationStatus


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    import gptme.config as config_module
    import gptme.config.user as user_module
    from gptme.config.core import _config_var

    home = tmp_path / "home"
    xdg = home / ".config"
    (xdg / "gptme").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "MODEL",
        "GPTME_MODEL",
        "GPTME_DING",
        "DING",
        "GPTME_CONTEXT_TREE",
        "CONTEXT_TREE",
        "GPTME_AUTOCOMMIT",
        "AUTOCOMMIT",
    ):
        monkeypatch.delenv(var, raising=False)
    config_file = xdg / "gptme" / "config.toml"
    monkeypatch.setattr(user_module, "config_path", str(config_file))
    monkeypatch.setattr(config_module, "config_path", str(config_file))
    token = _config_var.set(None)
    try:
        yield config_file
    finally:
        _config_var.reset(token)


def test_onboard_detects_stored_api_key(monkeypatch):
    key = "sk-ant-api03-abcdefghijklmnop"
    path = set_stored_api_key("anthropic", key)
    assert Path(path).parent == Path(os.environ["XDG_CONFIG_HOME"]) / "gptme"
    # The runtime / doctor / setup all see it:
    assert ("anthropic", "credentials.toml") in list_available_providers()

    providers = _detect_providers()
    assert providers["anthropic"][0], (
        f"stored key not detected: {providers['anthropic']}"
    )


def test_onboard_check_exit_code_with_stored_key():
    set_stored_api_key("anthropic", "sk-ant-api03-abcdefghijklmnop")
    assert _run_wizard(check_only=True) == 0


def test_onboard_test_provider_finds_stored_key(monkeypatch):
    set_stored_api_key("anthropic", "sk-ant-api03-abcdefghijklmnop")
    seen: dict[str, str] = {}

    def fake_validate(key: str, provider: str) -> tuple[ApiKeyValidationStatus, str]:
        seen["key"] = key
        return ApiKeyValidationStatus.VALID, ""

    monkeypatch.setattr("gptme.cli.onboard.validate_api_key_status", fake_validate)
    ok, msg = _test_provider("anthropic")
    assert ok, msg
    assert seen["key"] == "sk-ant-api03-abcdefghijklmnop"


def test_onboard_detected_stored_provider_is_testable(monkeypatch):
    """Every provider detection lists from credentials.toml must be testable
    (moonshot is a runtime provider not in onboarding's env-var table)."""
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    set_stored_api_key("moonshot", "sk-moonshot-abcdefghijklmnop")
    assert _detect_providers()["moonshot"][0]

    seen: dict[str, str] = {}

    def fake_validate(key: str, provider: str) -> tuple[ApiKeyValidationStatus, str]:
        seen[provider] = key
        return ApiKeyValidationStatus.VALID, ""

    monkeypatch.setattr("gptme.cli.onboard.validate_api_key_status", fake_validate)
    ok, msg = _test_provider("moonshot")
    assert ok, msg
    assert seen == {"moonshot": "sk-moonshot-abcdefghijklmnop"}


def test_onboard_unknown_provider_without_key_still_rejected():
    ok, msg = _test_provider("no-such-provider")
    assert not ok
    assert "Unknown provider" in msg


def test_onboard_unsupported_provider_does_not_claim_connection(monkeypatch):
    """An offered stored key is not evidence of a live connection."""
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    set_stored_api_key("opencode-go", "test-opencode-key")
    ok, msg = _test_provider("opencode-go")
    assert ok
    assert "not validated" in msg

    from rich.console import Console

    import gptme.cli.onboard as onboard

    console = Console(record=True)
    monkeypatch.setattr(onboard, "console", console)
    monkeypatch.setattr(onboard, "_select_provider", lambda _: "opencode-go")
    monkeypatch.setattr(onboard, "_get_default_model", lambda _: "opencode-go/test")
    monkeypatch.setattr(
        onboard.Prompt, "ask", lambda *args, **kwargs: "opencode-go/test"
    )
    monkeypatch.setattr(onboard, "_create_config", lambda *args: Path("config.toml"))
    assert onboard._run_wizard() == 0
    output = console.export_text()
    assert "not validated" in output
    assert "Successfully connected" not in output
