"""Regression tests for config file encoding.

TOML is defined to be UTF-8. Every `open()` in `gptme/config/` used to omit
`encoding=`, so config files were read and written with the platform's *preferred*
encoding — a legacy codepage on a stock Windows install (cp936 on a Chinese
system, cp1252 on a Western one). The fields most likely to hold non-ASCII text
are exactly the ones a user writes about themselves: `[user] about`,
`response_preference`, `name`.
"""

import builtins
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import tomlkit

import gptme.config.project as project_mod
import gptme.config.user as user_mod
from gptme.config import ChatConfig

# A codec that is the platform default on a stock Windows install but cannot
# represent most of Unicode. `ascii` is used where the *read* side is under test:
# cp1252 maps every byte, so it round-trips undecodable input into mojibake
# without raising, and would not detect a missing `encoding=` on a read.
LEGACY_CODEC = "cp1252"

ABOUT = "我是一名开发者 — I write in 中文 and café-French"


@contextmanager
def legacy_default_encoding(codec: str = LEGACY_CODEC):
    """Make encoding-less `open()` calls behave as they do under a legacy locale.

    Monkeypatching `locale.getpreferredencoding` does not work: CPython reads the
    locale encoding at the C level, so `open()` ignores the patched function. This
    shim instead supplies a codec in exactly the position CPython would supply the
    locale's — only when the caller passed no `encoding` — so these tests fail on a
    machine of any locale when `encoding=` is missing, and pass on a machine of any
    locale when it is present.
    """
    real_open = builtins.open

    def shim(file, mode="r", *args, **kwargs):
        if "b" not in mode and kwargs.get("encoding") is None and len(args) < 2:
            kwargs["encoding"] = codec
        return real_open(file, mode, *args, **kwargs)

    with patch.object(builtins, "open", shim):
        yield


def test_load_user_config_reads_non_ascii_about(tmp_path: Path):
    """A `[user] about` in the user's own language must load, not raise.

    Read side: the bytes already on disk were enough to break config loading
    entirely, so gptme would not start for such a user.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'[user]\nabout = "{ABOUT}"\n', encoding="utf-8")

    with legacy_default_encoding("ascii"):
        config = user_mod.load_user_config(str(config_file))

    assert config.user.about == ABOUT


def test_load_user_config_reads_non_ascii_from_local_override(tmp_path: Path):
    """The `config.local.toml` merge path opens a second file of its own."""
    config_file = tmp_path / "config.toml"
    config_file.write_text('[user]\nabout = "placeholder"\n', encoding="utf-8")
    (tmp_path / "config.local.toml").write_text(
        f'[user]\nabout = "{ABOUT}"\n', encoding="utf-8"
    )

    with legacy_default_encoding("ascii"):
        config = user_mod.load_user_config(str(config_file))

    assert config.user.about == ABOUT


def test_set_config_value_writes_valid_utf8_toml(tmp_path: Path, monkeypatch):
    """Write side, and this is the worst case.

    Under a legacy codepage the value either raises on encode or — for text the
    codepage happens to cover — is written in that codepage, producing a file that
    is not valid UTF-8 and therefore not valid TOML for any other reader,
    including `tomllib` and gptme's own `ChatConfig` loader.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    with legacy_default_encoding():
        user_mod.set_config_value("user.about", ABOUT, reload=False)

    # Decode the raw bytes as strict UTF-8 — a bare read_text() would use the
    # platform encoding and hide the defect. This raises UnicodeDecodeError if the
    # file was written in a codepage.
    text = config_file.read_bytes().decode("utf-8")
    assert tomlkit.loads(text).unwrap()["user"]["about"] == ABOUT


def test_set_config_value_ascii_still_works(tmp_path: Path, monkeypatch):
    """Control: the case the old code got right must not regress."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    with legacy_default_encoding():
        user_mod.set_config_value("user.name", "Alice", reload=False)

    doc = tomlkit.loads(config_file.read_text(encoding="utf-8")).unwrap()
    assert doc["user"]["name"] == "Alice"


def test_user_config_round_trips_non_ascii(tmp_path: Path, monkeypatch):
    """Write then read back: the two sides must agree on the encoding.

    A mismatch is silent — the write succeeds, and the failure only appears the
    next time the config is loaded.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    with legacy_default_encoding():
        user_mod.set_config_value("user.about", ABOUT, reload=False)
        config = user_mod.load_user_config(str(config_file))

    assert config.user.about == ABOUT


def test_project_config_reads_non_ascii(tmp_path: Path):
    """`gptme.toml` is committed to repositories, so it is shared across machines.

    A prompt written on a UTF-8 machine must load on a Windows one.
    """
    (tmp_path / "gptme.toml").write_text(f'prompt = "{ABOUT}"\n', encoding="utf-8")

    project_mod._get_project_config_cached.cache_clear()
    with legacy_default_encoding("ascii"):
        config = project_mod.get_project_config(tmp_path, quiet=True)

    assert config is not None
    assert config.prompt == ABOUT


def test_chat_config_save_writes_valid_utf8(tmp_path: Path):
    """`ChatConfig.save()` writes via `tempfile.NamedTemporaryFile(mode="w")`.

    That call takes `encoding` too and omitted it, so the atomic write produced a
    `config.toml` in the platform codepage — which `from_logdir` then reads with
    `tomllib`, which is UTF-8 only and raises.
    """
    logdir = tmp_path / "conv"
    logdir.mkdir()
    # Point workspace at the path save() would symlink to, so it skips the symlink
    # (which needs a privilege the default Windows account does not have).
    workspace = logdir / "workspace"
    workspace.mkdir()

    with legacy_default_encoding():
        ChatConfig(
            _logdir=logdir, model="test-model", name=ABOUT, workspace=workspace
        ).save()

    text = (logdir / "config.toml").read_bytes().decode("utf-8")
    assert tomlkit.loads(text).unwrap()["chat"]["name"] == ABOUT
    assert ChatConfig.from_logdir(logdir).name == ABOUT
