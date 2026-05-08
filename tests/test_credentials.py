from pathlib import Path

from gptme.credentials import (
    _get_credentials_path,
    get_stored_api_key,
    list_stored_credentials,
    mask_secret,
    set_stored_api_key,
)


def test_stored_credentials_roundtrip(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = set_stored_api_key("openrouter", "sk-or-test-12345678")

    assert path == _get_credentials_path()
    assert get_stored_api_key("openrouter") == "sk-or-test-12345678"
    assert list_stored_credentials() == [("openrouter", "sk-or-test-12345678")]


def test_stored_credentials_permissions(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = set_stored_api_key("anthropic", "sk-ant-test")

    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_mask_secret():
    assert mask_secret("abcdefgh") == "********"
    assert mask_secret("sk-ant-12345678") == "sk-a...5678"
