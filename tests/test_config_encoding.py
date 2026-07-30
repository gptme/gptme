"""Regression tests for config file encoding.

TOML is defined to be UTF-8. Every `open()` in `gptme/config/` used to omit
`encoding=`, so config files were read and written with the platform's *preferred*
encoding — a legacy codepage on a stock Windows install (cp936 on a Chinese
system, cp1252 on a Western one). The fields most likely to hold non-ASCII text
are exactly the ones a user writes about themselves: `[user] about`,
`response_preference`, `name`.
"""

import builtins
import codecs
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
def legacy_locale_codec(codec: str, utf8_mode: bool = False):
    """Make the read-side fallback behave as it does on a legacy-codepage machine.

    `_read_config_text` reads bytes and asks the `locale` module what to fall back
    to, so — unlike the writers — it is not reached by the `open()` shim above. It
    must be patched at those calls instead, or the upgrade-path tests only exercise
    the fallback on a machine whose locale happens to be a legacy code page.

    `utf8_mode=True` reproduces Python's UTF-8 mode (`-X utf8`, `PYTHONUTF8=1`):
    `getpreferredencoding(False)` reports "utf-8" while `getencoding()` still
    reports the real code page. A fallback that consults only the former is blind
    to the codec that wrote the file on exactly the machine that wrote it.
    """
    preferred = "utf-8" if utf8_mode else codec
    with (
        patch.object(user_mod.locale, "getpreferredencoding", lambda *a: preferred),
        patch.object(user_mod.locale, "getencoding", lambda: codec, create=True),
        patch.object(
            user_mod.locale, "getdefaultlocale", lambda: ("zh_CN", codec), create=True
        ),
    ):
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

# A byte no candidate can decode, so the read falls all the way through to
# `errors="replace"`. 0xff is invalid in UTF-8 and, unlike a single-byte code page
# that maps every byte, invalid in cp936 too — which is what makes cp936 the locale
# to patch in for the replacement path.
UNDECODABLE_BYTE = b"\xff"


@pytest.mark.parametrize("utf8_mode", [False, True], ids=["locale", "utf8-mode"])
def test_legacy_encoded_user_config_is_still_readable(tmp_path: Path, utf8_mode: bool):
    """A config an older gptme wrote in cp936 must not break loading.

    Parametrised over UTF-8 mode because that is the case a fallback built on
    `getpreferredencoding` alone cannot serve: it reports "utf-8" there, hiding the
    very code page that wrote the file.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')

    with legacy_locale_codec("cp936", utf8_mode=utf8_mode):
        config = user_mod.load_user_config(str(config_file))

    assert config.user.about == "我是一名开发者"


@pytest.mark.parametrize("utf8_mode", [False, True], ids=["locale", "utf8-mode"])
def test_legacy_encoded_chat_config_is_still_readable(tmp_path: Path, utf8_mode: bool):
    """Same for a per-conversation config.

    On `master` this case is worse than a fallback-less read: `tomllib.load`
    raises `UnicodeDecodeError`, which `_CHAT_CONFIG_LOAD_ERRORS` did not list,
    so it escaped `from_logdir` entirely instead of degrading to defaults.

    Under UTF-8 mode the symptom is different but no better: the settings decode to
    replacement characters, so `from_logdir` silently returns a config whose `name`
    is mojibake rather than the one on disk.
    """
    logdir = tmp_path / "conv"
    logdir.mkdir()
    (logdir / "config.toml").write_bytes(
        b'[chat]\nname = "' + LEGACY_BYTES_ABOUT + b'"\nmodel = "test-model"\n'
    )

    with legacy_locale_codec("cp936", utf8_mode=utf8_mode):
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

    When no candidate codec can decode the bytes, re-raising would send a
    `UnicodeDecodeError` up to callers — the very failure the fallback exists to
    prevent, just narrowed to the platforms where no fallback can help. Callers
    handle TOML parse errors, not decode errors, so `errors="replace"` is the right
    outcome. The aliases cover `codecs.lookup` normalisation, which is what stops
    "UTF-8" and "utf8" from being retried as if they were a second codec.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')

    with legacy_locale_codec(locale_codec):
        text = user_mod._read_config_text(config_file)

    assert isinstance(text, str)


def test_legacy_codec_candidates_excludes_utf8_and_deduplicates():
    """The candidate list must never re-offer a codec UTF-8 already covers.

    `_read_config_text` has decoded as UTF-8 before consulting this list, so a
    "utf-8" entry — under an ASCII/UTF-8 locale, or from
    `getpreferredencoding` under UTF-8 mode — is a wasted retry that would decode
    the same bytes the same failing way. Aliases must collapse too, since
    `getencoding()` and `getpreferredencoding()` routinely spell one codec two ways.
    """
    with legacy_locale_codec("cp936", utf8_mode=True):
        candidates = user_mod._legacy_codec_candidates()

    canonical = [codecs.lookup(c).name for c in candidates]
    assert "utf-8" not in canonical
    assert canonical == [codecs.lookup("cp936").name]

    # Both sources agreeing must not yield the codec twice.
    with legacy_locale_codec("cp1252"):
        assert [codecs.lookup(c).name for c in user_mod._legacy_codec_candidates()] == [
            codecs.lookup("cp1252").name
        ]


def test_a_codec_that_cannot_decode_hands_over_to_the_next(tmp_path: Path):
    """Candidates are tried strictly, so a wrong codec does not win by mojibake.

    Decoding each candidate with errors="replace" would let the first one always
    "succeed", making every later candidate dead code. Here the preferred encoding
    (UTF-8 mode) cannot decode the bytes and `getencoding()` can, so the real code
    page must be the one that is used.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')

    with legacy_locale_codec("cp936", utf8_mode=True):
        text = user_mod._read_config_text(config_file)

    assert "我是一名开发者" in text
    assert "�" not in text  # no replacement characters


def test_a_successful_decode_does_not_identify_the_codec():
    """Why the fallback cannot be trusted, and why no cheap check can rescue it.

    This is the premise the rest of these tests rest on, so it is asserted rather
    than assumed. The same cp936 bytes decode without error, into the wrong text,
    under codecs of both kinds:

    - single-byte tables map each byte independently and so accept almost anything;
    - multi-byte code pages *do* have invalid sequences, but real text routinely
      satisfies more than one of them, so raising is not something they can be
      relied on to do.

    "Does this codec reject some bytes?" therefore does not separate the safe cases
    from the unsafe ones -- cp1252 leaves five bytes undefined and none of them occur
    here. Hence: no classifier, treat every fallback as a guess.
    """
    for codec in ("cp1252", "latin-1", "cp437", "big5", "cp949", "cp932"):
        decoded = LEGACY_BYTES_ABOUT.decode(codec)  # no error
        assert decoded != "我是一名开发者", f"{codec} unexpectedly round-tripped"

    # And the parser is no backstop: the mojibake is still valid TOML.
    mojibake = (b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n').decode("cp1252")
    assert tomlkit.loads(mojibake).unwrap()["user"]["about"] != "我是一名开发者"

    # The damage is undone only by the bytes, which is what the backup preserves.
    assert LEGACY_BYTES_ABOUT.decode("cp1252").encode("cp1252") == LEGACY_BYTES_ABOUT


@pytest.mark.parametrize("locale_codec", ["cp1252", "cp936"])
def test_every_legacy_fallback_is_recorded_as_unverified(
    tmp_path: Path, locale_codec: str
):
    """Both the wrong codec and the right one are marked; neither can be told apart.

    cp1252 is the damaging case -- it decodes these bytes into mojibake -- and cp936
    is the one that happens to be correct. `_read_config_text` cannot distinguish
    them, so it must not try: both get recorded, and the cost of being wrong about
    cp936 is one backup file nobody needs.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec(locale_codec):
        text = user_mod._read_config_text(config_file)

    assert isinstance(text, str)
    assert str(config_file.resolve()) in user_mod._encoding_unverified


def test_replacement_decoding_is_recorded_as_unverified(tmp_path: Path):
    """The last-resort read is a guess too, and the one that loses the most.

    A legacy-codec fallback at least keeps the bytes' meaning — mojibake
    re-encodes to the original bytes — whereas U+FFFD discards which byte it
    stood for, so nothing but the backup can undo it. Leaving this path unmarked
    would exempt the most damaging decode from the protection the milder ones get.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "caf' + UNDECODABLE_BYTE + b'"\n')
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec("cp936"):
        text = user_mod._read_config_text(config_file)

    assert "�" in text, "test byte was decodable after all"
    assert str(config_file.resolve()) in user_mod._encoding_unverified


def test_replacement_decoded_config_can_still_be_valid_toml(tmp_path: Path):
    """Reaching the parser is not the same as being rejected by it.

    `errors="replace"` is chosen so the parser decides what happens, and for a
    file that is corrupt in its structure the parser does reject it (see
    `test_undecodable_config_fails_at_the_parser_not_the_decoder`). But an
    undecodable byte *inside a quoted value* leaves the document well-formed:
    U+FFFD is an ordinary character to TOML. So the parser cannot be relied on as
    the backstop for this path either, which is why it is marked instead.
    """
    text = (b'[user]\nabout = "caf' + UNDECODABLE_BYTE + b'"\n').decode(
        "utf-8", errors="replace"
    )

    assert tomlkit.loads(text).unwrap()["user"]["about"] == "caf�"

    # And unlike a wrong-codec read, this one cannot be undone from the text:
    # the byte is gone, not merely misinterpreted.
    assert "caf�".encode(errors="replace") != b"caf" + UNDECODABLE_BYTE


def test_replacement_decoded_config_is_backed_up_before_being_rewritten(
    tmp_path: Path, monkeypatch
):
    """End to end: a save must not be what turns U+FFFD into the file's contents.

    This is the case the parser lets through — a well-formed document holding a
    replacement character — so without the marker the save proceeds, writes the
    U+FFFD out as UTF-8, and the byte it replaced is gone for good with no `.orig`
    beside it.
    """
    config_file = tmp_path / "config.toml"
    original = b'[user]\nabout = "caf' + UNDECODABLE_BYTE + b'"\n'
    config_file.write_bytes(original)
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec("cp936"), legacy_default_encoding("cp936"):
        user_mod.set_config_value("user.name", "Alice", reload=False)

    backup = Path(str(config_file) + ".orig")
    assert backup.exists(), "original bytes were discarded"
    assert backup.read_bytes() == original


def test_unverified_config_is_backed_up_before_being_rewritten(
    tmp_path: Path, monkeypatch
):
    """The original bytes must survive the rewrite that would otherwise erase them.

    A misread is reversible — the bytes still mean what they meant, they were only
    read through the wrong table — but only while the bytes exist. Rewriting as
    UTF-8 without a copy makes the loss permanent.
    """
    config_file = tmp_path / "config.toml"
    original = b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n'
    config_file.write_bytes(original)
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec("cp1252"), legacy_default_encoding("cp1252"):
        user_mod.set_config_value("user.name", "Alice", reload=False)

    backup = Path(str(config_file) + ".orig")
    assert backup.exists(), "original bytes were discarded"
    assert backup.read_bytes() == original
    # And the loss is recoverable from the backup.
    assert LEGACY_BYTES_ABOUT.decode("cp936") == "我是一名开发者"


def test_utf8_config_is_not_backed_up(tmp_path: Path, monkeypatch):
    """The normal path stays clean: no fallback, no `.orig` beside every config.

    A UTF-8 config is read by the codec TOML specifies, so there is nothing to
    second-guess. Only files that went through the legacy fallback get a copy.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text('[user]\nabout = "我是一名开发者"\n', encoding="utf-8")
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec("cp1252"), legacy_default_encoding("cp1252"):
        user_mod.set_config_value("user.name", "Alice", reload=False)

    assert not Path(str(config_file) + ".orig").exists()
    doc = tomlkit.loads(config_file.read_bytes().decode("utf-8")).unwrap()
    assert doc["user"]["about"] == "我是一名开发者"


def test_backup_is_written_once_not_overwritten_by_a_second_save(
    tmp_path: Path, monkeypatch
):
    """A second save must not replace the original with the already-garbled file."""
    config_file = tmp_path / "config.toml"
    original = b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n'
    config_file.write_bytes(original)
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec("cp1252"), legacy_default_encoding("cp1252"):
        user_mod.set_config_value("user.name", "Alice", reload=False)
        user_mod.set_config_value("user.name", "Bob", reload=False)

    assert Path(str(config_file) + ".orig").read_bytes() == original


@contextmanager
def backup_writes_failing():
    """Make writing the `.orig` copy fail, as a full disk or read-only dir would.

    Both the staging file and the final name are covered, since the copy is made
    under a temporary name and renamed — a probe that only fails on `.orig` would
    silently miss the write and report a pass.
    """
    real_write_bytes = Path.write_bytes

    def shim(self, data):
        if ".orig" in str(self):
            raise OSError(28, "No space left on device")
        return real_write_bytes(self, data)

    with patch.object(Path, "write_bytes", shim):
        yield


def test_failed_backup_aborts_the_rewrite_instead_of_destroying_the_bytes(
    tmp_path: Path, monkeypatch
):
    """A backup that did not happen removes the justification for the write.

    The backup exists because rewriting a guessed decoding as UTF-8 is the step
    that makes a misread permanent. If the copy fails, logging and continuing
    performs exactly the destruction the backup was added to prevent — and it is
    the worst case, because the values are garbled *and* unrecoverable.

    Refusing to save is recoverable: the user is told why and can copy the file
    by hand. So the error propagates and nothing is written.
    """
    config_file = tmp_path / "config.toml"
    original = b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n'
    config_file.write_bytes(original)
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    user_mod._encoding_unverified.clear()

    with (
        legacy_locale_codec("cp1252"),
        legacy_default_encoding("cp1252"),
        backup_writes_failing(),
        pytest.raises(OSError, match="Refusing to rewrite"),
    ):
        user_mod.set_config_value("user.name", "Alice", reload=False)

    # The bytes that could not be copied are still where they were.
    assert config_file.read_bytes() == original
    assert LEGACY_BYTES_ABOUT.decode("cp936") == "我是一名开发者"


def test_failed_backup_leaves_no_partial_orig_file(tmp_path: Path, monkeypatch):
    """A truncated `.orig` is as destructive as none, and reads as success.

    The copy is staged under a temporary name and renamed, so a failure part way
    through leaves nothing behind. Were it written to `.orig` directly, the next
    save would see the file exist, treat the config as already preserved, and
    rewrite it — with only a fragment of the original kept.
    """
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n')
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    user_mod._encoding_unverified.clear()

    with (
        legacy_locale_codec("cp1252"),
        legacy_default_encoding("cp1252"),
        backup_writes_failing(),
        pytest.raises(OSError, match="Refusing to rewrite"),
    ):
        user_mod.set_config_value("user.name", "Alice", reload=False)

    assert [p.name for p in tmp_path.iterdir() if ".orig" in p.name] == []


def test_a_later_save_retries_a_backup_that_failed(tmp_path: Path, monkeypatch):
    """The file still holds unpreserved bytes, so the next save must try again.

    The marker is what records "these bytes have not been copied yet". Clearing it
    on failure — which a `finally` would — makes the *next* save skip the backup
    and rewrite the file, so a single transient disk error would lose the original
    at the following save instead of the current one.
    """
    config_file = tmp_path / "config.toml"
    original = b'[user]\nabout = "' + LEGACY_BYTES_ABOUT + b'"\n'
    config_file.write_bytes(original)
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec("cp1252"), legacy_default_encoding("cp1252"):
        with (
            backup_writes_failing(),
            pytest.raises(OSError, match="Refusing to rewrite"),
        ):
            user_mod.set_config_value("user.name", "Alice", reload=False)
        assert str(config_file.resolve()) in user_mod._encoding_unverified

        # Disk error cleared; the save that follows preserves the bytes.
        user_mod.set_config_value("user.name", "Alice", reload=False)

    assert Path(str(config_file) + ".orig").read_bytes() == original


def test_failed_backup_aborts_a_chat_config_save_too(tmp_path: Path):
    """The conversation config reaches the same backup, so it gets the same refusal.

    `ChatConfig.save()` writes atomically via a rename, which would replace the
    original bytes just as thoroughly as a direct write.
    """
    chat_config_path = tmp_path / "config.toml"
    original = b'name = "' + LEGACY_BYTES_ABOUT + b'"\n'
    chat_config_path.write_bytes(original)
    user_mod._encoding_unverified.clear()

    with legacy_locale_codec("cp1252"):
        config = ChatConfig.from_logdir(tmp_path)
        assert str(chat_config_path.resolve()) in user_mod._encoding_unverified
        with (
            backup_writes_failing(),
            pytest.raises(OSError, match="Refusing to rewrite"),
        ):
            config.save()

    assert chat_config_path.read_bytes() == original
