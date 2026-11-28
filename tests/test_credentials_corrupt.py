"""A malformed credentials.toml crashes provider discovery even when the
user's key is in the environment (startup auto-detect, /account, doctor)."""

from pathlib import Path

from gptme.config import get_config


def test_corrupt_credentials_does_not_break_provider_discovery(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cred = tmp_path / "gptme" / "credentials.toml"
    cred.parent.mkdir(parents=True)
    # e.g. a hand-edit that forgot quotes
    cred.write_text("[providers]\nanthropic = sk-ant-unquoted\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-env-key")

    from gptme.llm import list_available_providers

    providers = dict(list_available_providers(get_config()))
    assert "openai" in providers


def test_corrupt_credentials_get_stored_api_key_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cred = tmp_path / "gptme" / "credentials.toml"
    cred.parent.mkdir(parents=True)
    cred.write_text("[providers\n", encoding="utf-8")

    from gptme.credentials import get_stored_api_key

    assert get_stored_api_key("anthropic") is None


def test_non_utf8_credentials_does_not_break_reads_but_blocks_writes(
    monkeypatch, tmp_path
):
    import pytest

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cred = tmp_path / "gptme" / "credentials.toml"
    cred.parent.mkdir(parents=True)
    raw = b'[providers]\nanthropic = "sk-\xff\xfe"\n'
    cred.write_bytes(raw)

    from gptme.credentials import get_stored_api_key, set_stored_api_key

    assert get_stored_api_key("anthropic") is None
    with pytest.raises(ValueError, match="Cannot parse"):
        set_stored_api_key("openai", "sk-new")
    assert cred.read_bytes() == raw
