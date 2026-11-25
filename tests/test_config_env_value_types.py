"""Non-string ``[env]`` values in project gptme.toml / chat config.toml are
accepted unvalidated and later crash ``Config.get_env_bool``.

Writing ``[env] COSTS = true`` (a natural TOML boolean) is accepted by
``ProjectConfig.from_dict`` and ``ChatConfig.from_dict`` (gptme/config/chat.py:217),
then ``get_env_bool`` calls ``.lower()`` on a bool/int -> AttributeError at
runtime (e.g. gptme/chat.py:1648 ``get_env_bool("GPTME_COSTS")``).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    for k in ("COSTS", "GPTME_COSTS", "PRUNE_TOOL_OUTPUT", "GPTME_PRUNE_TOOL_OUTPUT"):
        monkeypatch.delenv(k, raising=False)
    from gptme.config.project import _get_project_config_cached

    _get_project_config_cached.cache_clear()
    yield
    _get_project_config_cached.cache_clear()


def test_project_env_toml_bool_does_not_crash_get_env_bool(tmp_path):
    from gptme.config import Config

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "gptme.toml").write_text("[env]\nCOSTS = true\nPRUNE_TOOL_OUTPUT = 1\n")
    cfg = Config.from_workspace(ws)
    assert cfg.get_env_bool("GPTME_COSTS") is True
    assert cfg.get_env_bool("PRUNE_TOOL_OUTPUT") is True


def test_chat_config_env_non_string_rejected_or_stringified(tmp_path):
    from gptme.config.chat import ChatConfig

    logdir = tmp_path / "log"
    logdir.mkdir()
    (logdir / "config.toml").write_text(
        f'[chat]\nworkspace = "{tmp_path}"\n\n[env]\nCOSTS = true\n'
    )
    chat = ChatConfig.from_logdir(logdir)
    assert all(isinstance(v, str) for v in chat.env.values()), chat.env


def test_chat_null_env_keeps_lower_layer_value():
    from gptme.config import Config
    from gptme.config.chat import ChatConfig
    from gptme.config.user import UserConfig

    cfg = Config(
        user=UserConfig(env={"EXAMPLE_KEY": "user-value"}),
        chat=ChatConfig.from_dict({"env": {"EXAMPLE_KEY": None}}),
    )
    assert cfg.get_env("EXAMPLE_KEY") == "user-value"
    assert cfg.chat is not None
    assert "EXAMPLE_KEY" not in cfg.chat.env


@pytest.mark.parametrize("value", [["a"], {"nested": "a"}])
@pytest.mark.parametrize("layer", ["chat", "project"])
def test_env_rejects_compound_values(tmp_path, value, layer):
    from gptme.config.chat import ChatConfig
    from gptme.config.models import ProjectConfig

    with pytest.raises(ValueError, match="env.*EXAMPLE_KEY.*scalar"):
        if layer == "chat":
            ChatConfig.from_dict({"env": {"EXAMPLE_KEY": value}})
        elif layer == "project":
            ProjectConfig.from_dict({"env": {"EXAMPLE_KEY": value}}, tmp_path)


@pytest.mark.parametrize("value", [["a"], {"nested": "a"}])
@pytest.mark.parametrize("filename", ["config.toml", "config.local.toml"])
@pytest.mark.parametrize("runtime", [False, True])
def test_user_env_skips_compound_values(tmp_path, caplog, value, filename, runtime):
    import tomlkit

    from gptme.config.user import load_user_config

    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    if runtime:
        (tmp_path / "config.runtime.toml").write_text(
            '[env]\nRUNTIME_KEY = "default"\n', encoding="utf-8"
        )
    (tmp_path / filename).write_text(
        tomlkit.dumps({"env": {"EXAMPLE_KEY": value, "COSTS": False}}),
        encoding="utf-8",
    )
    cfg = load_user_config(str(path))
    assert "EXAMPLE_KEY" not in cfg.env
    assert cfg.env["COSTS"] == "false"
    if runtime:
        assert cfg.env["RUNTIME_KEY"] == "default"
    assert "env.EXAMPLE_KEY must be a scalar value" in caplog.text


@pytest.mark.parametrize("value", [["a"], {"nested": "a"}])
def test_runtime_env_rejects_compound_values_even_if_overridden(tmp_path, value):
    import tomlkit

    from gptme.config.user import load_user_config

    path = tmp_path / "config.toml"
    path.write_text('[env]\nEXAMPLE_KEY = "valid"\n', encoding="utf-8")
    (tmp_path / "config.runtime.toml").write_text(
        tomlkit.dumps({"env": {"EXAMPLE_KEY": value}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="env.*EXAMPLE_KEY.*scalar"):
        load_user_config(str(path))
