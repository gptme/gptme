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

import pytest
import tomlkit
from tomlkit.exceptions import TOMLKitError

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


@contextmanager
def legacy_locale_codec(codec: str):
    """Make the read-side fallback behave as it does under a legacy locale.

    `_read_config_text` reads bytes and asks `locale.getpreferredencoding` what to
    fall back to, so — unlike the writers — it is not reached by the `open()` shim
    above. It must be patched at that call instead, or the upgrade-path tests only
    exercise the fallback on a machine whose locale happens to be a legacy code
    page, and assert the *opposite* behaviour (a re-raise) on a UTF-8 one.
    """
    with patch.object(user_mod.locale, "getpreferredencoding", lambda *args: codec):
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


# Upgrade path: gptme wrote these files without naming an encoding until this
# change, so a config left behind by an older install on Windows may be in a
# legacy code page. Strict UTF-8 decoding alone would make gptme fail to start
# for exactly the users this change is meant to help.

LEGACY_BYTES_ABOUT = "我是一名开发者".encode("cp936")


def test_legacy_encoded_user_config_is_still_readable(tmp_path: Path):
    """A config an older gptme wrote in cp936 must not break loading."""
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')

    with legacy_locale_codec("cp936"):
        config = user_mod.load_user_config(str(config_file))

    assert config.user.about == "我是一名开发者"


def test_legacy_encoded_chat_config_is_still_readable(tmp_path: Path):
    """Same for a per-conversation config.

    On `master` this case is worse than a fallback-less read: `tomllib.load`
    raises `UnicodeDecodeError`, which `_CHAT_CONFIG_LOAD_ERRORS` did not list,
    so it escaped `from_logdir` entirely instead of degrading to defaults.
    """
    logdir = tmp_path / "conv"
    logdir.mkdir()
    (logdir / "config.toml").write_bytes(
        b'[chat]\nname = "' + LEGACY_BYTES_ABOUT + b'"\nmodel = "test-model"\n'
    )

    with legacy_locale_codec("cp936"):
        config = ChatConfig.from_logdir(logdir)

    assert config.name == "我是一名开发者"
    assert config.model == "test-model"


def test_legacy_encoded_config_is_rewritten_as_utf8(tmp_path: Path, monkeypatch):
    """The fallback is a read-side bridge only: the next write normalises the file."""
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    # Both shims: the read goes through the locale fallback, the write through `open()`.
    with legacy_locale_codec("cp936"), legacy_default_encoding("cp936"):
        user_mod.set_config_value("user.name", "Alice", reload=False)

    doc = tomlkit.loads(config_file.read_bytes().decode("utf-8")).unwrap()
    assert doc["user"]["about"] == "我是一名开发者"  # preserved, not mangled
    assert doc["user"]["name"] == "Alice"


def test_undecodable_config_fails_at_the_parser_not_the_decoder(tmp_path: Path):
    """Bytes no codec can make sense of must reach the parser, not raise on decode.

    A config that is neither UTF-8 nor valid in the locale codec is corrupt. The
    fallback decodes it with errors="replace" so that the *parser* decides what
    happens to it, which is what callers already handle -- `ChatConfig.from_logdir`
    catches TOML errors and degrades to defaults, and `_strip_unknown_config_keys`
    catches OSError. A UnicodeDecodeError escaping from the decode would not be.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "\xff\xfe\x00garbage"\n')

    with legacy_locale_codec("cp936"), pytest.raises(TOMLKitError):
        user_mod.load_user_config(str(config_file))


@pytest.mark.parametrize("locale_codec", ["UTF-8", "utf8", "cp936"])
def test_read_config_text_never_raises_unicode_decode_error(
    tmp_path: Path, locale_codec: str
):
    """The read helper must not leak `UnicodeDecodeError` for any locale.

    On a UTF-8 locale there is no second codec to try, and re-raising sent a
    `UnicodeDecodeError` up to callers — the very failure the fallback exists to
    prevent, just narrowed to the platforms where the fallback cannot help. Callers
    handle TOML parse errors, not decode errors, so `errors="replace"` is the right
    outcome in both cases. The aliases cover `codecs.lookup` normalisation.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')

    with legacy_locale_codec(locale_codec):
        text = user_mod._read_config_text(config_file)

    assert isinstance(text, str)
