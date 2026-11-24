"""`/setup` extra features must write the key gptme actually reads."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.cli.setup import _configure_extra_features
from gptme.config import get_config, reload_config


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


FEATURES = {
    "GPTME_DING": "Bell sound on completion",
    "GPTME_CONTEXT_TREE": "Context tree visualization",
    "GPTME_AUTOCOMMIT": "Automatic git commits",
}


def test_enabling_extra_feature_takes_effect():
    assert get_config().get_env_bool("GPTME_DING", False) is False
    # user answers "yes" to every "Enable ...?" prompt
    with patch("gptme.cli.setup.Confirm.ask", return_value=True):
        _configure_extra_features(FEATURES)
    reload_config()
    # This is exactly how gptme/util/sound.py:331 reads the setting.
    assert get_config().get_env_bool("GPTME_DING") is True
    assert get_config().get_env_bool("GPTME_CONTEXT_TREE") is True
