"""Verified config precedence/merging bugs in gptme/config/{core,user}.py.

Each test FAILS on master ed58254b because of the bug it names.
Run from the checkout:
  .venv/bin/python -m pytest /tmp/bob-scratch/2494/r1/test_config_precedence_bugs.py -q -p no:cacheprovider
"""

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    import gptme.config as config_module
    import gptme.config.user as user_module
    from gptme.config.core import _config_var

    cfg_dir = tmp_path / "gptme"
    cfg_dir.mkdir()
    config_file = cfg_dir / "config.toml"
    monkeypatch.setattr(user_module, "config_path", str(config_file))
    monkeypatch.setattr(config_module, "config_path", str(config_file))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    for var in ("GPTME_MODEL", "MODEL", "OPENAI_API_KEY", "GPTME_OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    token = _config_var.set(None)
    try:
        yield config_file
    finally:
        _config_var.reset(token)


# --- R1-1 ---------------------------------------------------------------
def test_reload_config_keeps_chat_config(isolated: Path):
    """reload_config() (called by set_config_value, e.g. `/account setup`)
    rebuilds Config without the active conversation's ChatConfig."""
    from gptme.config.chat import ChatConfig
    from gptme.config.core import Config, get_config, reload_config, set_config
    from gptme.config.user import load_user_config

    isolated.write_text("")
    chat = ChatConfig(env={"CHAT_ONLY": "from-chat"}, watch_autowake=False)
    set_config(Config(user=load_user_config(), chat=chat))
    assert get_config().get_env("CHAT_ONLY") == "from-chat"

    reload_config()

    assert get_config().chat is not None, "chat config dropped by reload_config"
    assert get_config().get_env("CHAT_ONLY") == "from-chat"


def test_set_config_value_mid_chat_keeps_chat_config(isolated: Path):
    """Same bug via the public writer: writing one user key mid-session
    discards the conversation's chat config (env, mcp, watch_autowake...)."""
    from gptme.config.chat import ChatConfig
    from gptme.config.core import Config, get_config, set_config
    from gptme.config.user import load_user_config, set_config_value

    isolated.write_text("")
    chat = ChatConfig(watch_autowake=False)
    set_config(Config(user=load_user_config(), chat=chat))

    set_config_value("env.MODEL", "anthropic/claude-sonnet-4-6")

    chat_after = get_config().chat
    assert chat_after is not None
    assert chat_after.watch_autowake is False


def test_reload_config_keeps_env_overrides(isolated: Path):
    """env_overrides (subagent per-thread THINKING_EFFORT) are lost on reload."""
    from gptme.config.core import Config, get_config, reload_config, set_config
    from gptme.config.user import load_user_config

    isolated.write_text("")
    set_config(
        Config(user=load_user_config(), env_overrides={"THINKING_EFFORT": "low"})
    )
    reload_config()
    assert get_config().get_env("THINKING_EFFORT") == "low"


# --- R1-3 ---------------------------------------------------------------
def test_toml_bool_true_in_env_section_does_not_crash_get_env_bool(isolated: Path):
    """[env] FOO = true (a TOML bool) is stored un-coerced; get_env returns the
    bool and get_env_bool calls True.lower() -> AttributeError."""
    from gptme.config.core import Config
    from gptme.config.user import load_user_config

    isolated.write_text("[env]\nLESSONS_AUTO_INCLUDE = true\n")
    config = Config(user=load_user_config(str(isolated)))
    assert config.get_env_bool("GPTME_LESSONS_AUTO_INCLUDE", False) is True


def test_toml_bool_false_in_env_section_is_honored(isolated: Path):
    """[env] SHELL_REDACT_SECRETS = false is falsy, so get_env's `or` chain
    skips it and get_env_bool returns the default True: the setting is ignored."""
    from gptme.config.core import Config
    from gptme.config.user import load_user_config

    isolated.write_text("[env]\nSHELL_REDACT_SECRETS = false\n")
    config = Config(user=load_user_config(str(isolated)))
    assert config.get_env_bool("GPTME_SHELL_REDACT_SECRETS", True) is False


# --- R1-4 ---------------------------------------------------------------
def test_env_source_ignores_empty_shell_var_like_get_env(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
):
    """An exported-but-empty OPENAI_API_KEY is treated as unset by get_env
    (value comes from config.local.toml), but get_user_config_env_source
    reports "env" because it only tests key membership in os.environ."""
    from gptme.config.core import Config
    from gptme.config.user import get_user_config_env_source, load_user_config

    isolated.write_text("")
    (isolated.parent / "config.local.toml").write_text(
        '[env]\nOPENAI_API_KEY = "sk-local"\n'
    )
    monkeypatch.setenv("OPENAI_API_KEY", "")

    config = Config(user=load_user_config(str(isolated)))
    assert config.get_env("OPENAI_API_KEY") == "sk-local"
    assert get_user_config_env_source("OPENAI_API_KEY", str(isolated)) == (
        "config.local.toml"
    )


def test_user_config_env_not_a_table_is_a_clear_error(isolated: Path):
    """`env = []` must fail with a config error, not AttributeError."""
    from gptme.config.user import load_user_config

    isolated.write_text("env = []\n")
    with pytest.raises(ValueError, match="env must be an object"):
        load_user_config(str(isolated))


# --- Greptile P1 (review 2026-11-25T18:19Z) -----------------------------
def test_malformed_user_env_entry_keeps_runtime_default(isolated: Path):
    """A table-valued [env] entry in the user file must not delete a valid
    lower-priority runtime default. Dropping it after the merge deleted
    TOOL_ALLOWLIST entirely, silently re-enabling the default tool set."""
    from gptme.config.user import get_user_config_runtime_path, load_user_config

    isolated.write_text("[env]\nTOOL_ALLOWLIST = { shell = true }\n")
    runtime = get_user_config_runtime_path(str(isolated))
    runtime.write_text('[env]\nTOOL_ALLOWLIST = "read,save"\n')

    config = load_user_config(str(isolated))

    assert config.env["TOOL_ALLOWLIST"] == "read,save"


# --- AI review P2 (round cap, 2026-11-26) -------------------------------
def test_env_source_skips_dropped_compound_values(isolated: Path):
    """A compound [env] value in config.local.toml is dropped before the merge,
    so it never shadows config.toml: the source report must name config.toml,
    the file the value actually comes from."""
    from gptme.config.core import Config
    from gptme.config.user import get_user_config_env_source, load_user_config

    isolated.write_text('[env]\nTEST_SETTING = "from-main"\n')
    (isolated.parent / "config.local.toml").write_text('[env]\nTEST_SETTING = ["x"]\n')

    assert Config(user=load_user_config(str(isolated))).get_env("TEST_SETTING") == (
        "from-main"
    )
    assert get_user_config_env_source("TEST_SETTING", str(isolated)) == "config.toml"
