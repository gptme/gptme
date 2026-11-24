"""Onboarding must see API keys stored in credentials.toml (written by /account setup)."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from gptme.cli.onboard import _detect_providers, _run_wizard, _test_provider
from gptme.credentials import set_stored_api_key
from gptme.llm import list_available_providers


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

    def fake_validate(key: str, provider: str) -> tuple[bool, str]:
        seen["key"] = key
        return True, ""

    monkeypatch.setattr("gptme.cli.onboard.validate_api_key", fake_validate)
    ok, msg = _test_provider("anthropic")
    assert ok, msg
    assert seen["key"] == "sk-ant-api03-abcdefghijklmnop"
