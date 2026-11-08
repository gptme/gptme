"""Tests for gptme.config.validate — read-only config corruption detector."""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest
import tomllib

if TYPE_CHECKING:
    from pathlib import Path

from gptme.config.validate import (
    detect_config_problems,
    fix_providers_corruption,
)

# ---------------------------------------------------------------------------
# Fixtures: corrupt and clean config contents
# ---------------------------------------------------------------------------

CORRUPT_PROVIDERS_TOML = """\
providers = [name = "fixture"
base_url = "http://127.0.0.1:37453/v1"
default_model = "fixture-model"
]
"""

UNRELATED_SETTING = 'model = "openai/gpt-4o-mini"\n'

CLEAN_PROVIDERS_TOML = """\
[[providers]]
name = "fixture"
base_url = "http://127.0.0.1:37453/v1"
default_model = "fixture-model"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, content: str) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(content, encoding="utf-8")
    return cfg


def _make_token(tmp_path: Path, data: object) -> Path:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    token = auth_dir / "gptme-cloud-aabbccdd1234.json"
    token.write_text(json.dumps(data), encoding="utf-8")
    return token


# ---------------------------------------------------------------------------
# detect_config_problems
# ---------------------------------------------------------------------------


class TestDetectClean:
    """Clean config → no problems."""

    def test_empty_root(self, tmp_path: Path) -> None:
        problems = detect_config_problems(config_root=tmp_path)
        assert problems == []

    def test_valid_providers(self, tmp_path: Path) -> None:
        _make_config(tmp_path, CLEAN_PROVIDERS_TOML)
        problems = detect_config_problems(config_root=tmp_path)
        assert problems == []

    def test_valid_token(self, tmp_path: Path) -> None:
        _make_token(tmp_path, {"access_token": "tok", "expires_at": 9999999999})
        problems = detect_config_problems(config_root=tmp_path)
        assert problems == []


@pytest.mark.parametrize("operation", ["exists", "read_text"])
def test_unreadable_config_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from pathlib import Path

    from click.testing import CliRunner

    from gptme.cli.main import main
    from gptme.config import user
    from gptme.llm import llm_gptme

    cfg = _make_config(tmp_path, CLEAN_PROVIDERS_TOML)
    original = cfg.read_bytes()
    real_operation = getattr(Path, operation)

    def denied(path: Path, *args: object, **kwargs: object) -> object:
        if path == cfg:
            raise PermissionError("config access denied")
        return real_operation(path, *args, **kwargs)

    # Python 3.11/3.12 Path.exists propagates PermissionError; model that
    # behavior explicitly even on newer Python versions that suppress it.
    monkeypatch.setattr(Path, operation, denied)
    monkeypatch.setattr(user, "get_user_config_paths", lambda: (cfg, None))
    monkeypatch.setattr(llm_gptme, "_TOKEN_DIR", tmp_path / "auth")

    problems = detect_config_problems(tmp_path)
    assert [(p.name, p.path) for p in problems] == [("config-unreadable", cfg)]
    result = CliRunner().invoke(main, ["--check-config"])
    assert result.exit_code == 1, result.output
    assert "config-unreadable" in result.output
    assert "repair:" in result.output
    assert cfg.read_bytes() == original
    assert not list(tmp_path.glob("*.bak"))


class TestDetectProvidersCorruption:
    """providers-not-array pattern (gptme/gptme#4806)."""

    def test_detected(self, tmp_path: Path) -> None:
        _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        problems = detect_config_problems(config_root=tmp_path)
        names = [p.name for p in problems]
        assert "providers-not-array" in names

    def test_repair_cmd_present(self, tmp_path: Path) -> None:
        _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        problems = detect_config_problems(config_root=tmp_path)
        p = next(x for x in problems if x.name == "providers-not-array")
        assert p.repair_cmd  # non-empty
        assert p.repair_cmd.lower().startswith(("gptme", "rm"))

    def test_no_false_positive_on_valid_toml_parse_error(self, tmp_path: Path) -> None:
        # A parse error without the providers = [ pattern should not raise.
        _make_config(tmp_path, "this is not [ valid toml !\n")
        problems = detect_config_problems(config_root=tmp_path)
        # Should not contain providers-not-array
        assert not any(p.name == "providers-not-array" for p in problems)


class TestDetectAuthCorruption:
    """auth-cache-invalid pattern (gptme/gptme#4797)."""

    def test_list_root(self, tmp_path: Path) -> None:
        _make_token(tmp_path, ["not", "an", "object"])
        problems = detect_config_problems(config_root=tmp_path)
        names = [p.name for p in problems]
        assert "auth-cache-invalid" in names

    def test_null_root(self, tmp_path: Path) -> None:
        _make_token(tmp_path, None)
        problems = detect_config_problems(config_root=tmp_path)
        names = [p.name for p in problems]
        assert "auth-cache-invalid" in names

    def test_repair_cmd_present(self, tmp_path: Path) -> None:
        _make_token(tmp_path, ["x"])
        problems = detect_config_problems(config_root=tmp_path)
        p = next(x for x in problems if x.name == "auth-cache-invalid")
        assert p.repair_cmd

    def test_dict_root_is_clean(self, tmp_path: Path) -> None:
        _make_token(tmp_path, {"access_token": "tok"})
        problems = detect_config_problems(config_root=tmp_path)
        assert not any(p.name == "auth-cache-invalid" for p in problems)


# ---------------------------------------------------------------------------
# fix_providers_corruption
# ---------------------------------------------------------------------------


class TestFixProvidersCorruption:
    """fix_providers_corruption repairs in place and leaves a backup."""

    def test_parses_after_fix(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        tomllib.loads(cfg.read_text(encoding="utf-8"))  # must not raise

    def test_providers_is_list_of_tables(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
        providers = parsed.get("providers")
        assert isinstance(providers, list)
        assert all(isinstance(p, dict) for p in providers)

    def test_unrelated_setting_preserved(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
        assert parsed.get("model") == "openai/gpt-4o-mini"

    def test_backup_left_behind(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        bak = fix_providers_corruption(cfg)
        assert bak.exists()
        assert ".bak" in bak.name

    def test_backup_contains_original(self, tmp_path: Path) -> None:
        original = UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML
        cfg = _make_config(tmp_path, original)
        bak = fix_providers_corruption(cfg)
        assert bak.read_text(encoding="utf-8") == original

    def test_detect_no_longer_flags_after_fix(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML)
        fix_providers_corruption(cfg)
        problems = detect_config_problems(config_root=tmp_path)
        assert not any(p.name == "providers-not-array" for p in problems)

    def test_no_unrecognised_block_leaves_file_unchanged(self, tmp_path: Path) -> None:
        # Refuse clean or unrecognized input without even creating a backup.
        content = CLEAN_PROVIDERS_TOML
        cfg = _make_config(tmp_path, content)
        with pytest.raises(ValueError, match="no safe providers repair"):
            fix_providers_corruption(cfg)
        assert cfg.read_text(encoding="utf-8") == content
        assert not list(tmp_path.glob("*.bak"))


def test_repair_keeps_following_root_settings(tmp_path: Path) -> None:
    cfg = _make_config(
        tmp_path,
        CORRUPT_PROVIDERS_TOML + 'env.FOO = "bar"\n[prompt]\nfiles = ["README.md"]\n',
    )
    fix_providers_corruption(cfg)
    data = tomllib.loads(cfg.read_text())
    assert data["env"] == {"FOO": "bar"}
    assert data["prompt"] == {"files": ["README.md"]}
    assert data["providers"] == tomllib.loads(CLEAN_PROVIDERS_TOML)["providers"]


@pytest.mark.parametrize(
    "providers",
    [
        "providers = []\n",
        'providers = [\n{name = "local", base_url = "http://localhost/v1"}\n]\n',
    ],
)
def test_valid_array_with_other_error_is_not_repaired(
    tmp_path: Path, providers: str
) -> None:
    original = providers + "broken = [\n"
    cfg = _make_config(tmp_path, original)
    problems = detect_config_problems(tmp_path)
    assert [p.name for p in problems] == ["config-invalid"]
    with pytest.raises(ValueError, match="no safe providers repair"):
        fix_providers_corruption(cfg)
    assert cfg.read_text() == original
    assert not list(tmp_path.glob("*.bak"))


def test_invalid_repair_is_not_written(tmp_path: Path) -> None:
    original = CORRUPT_PROVIDERS_TOML + "broken = [\n"
    cfg = _make_config(tmp_path, original)
    with pytest.raises(ValueError, match="no safe providers repair"):
        fix_providers_corruption(cfg)
    assert cfg.read_text() == original
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize("legacy_index", [0, 1])
@pytest.mark.parametrize("content", ["[]", "null"])
def test_default_scan_finds_legacy_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_index: int, content: str
) -> None:
    from gptme.config import user
    from gptme.llm import llm_gptme

    monkeypatch.setattr(
        user, "get_user_config_paths", lambda: (tmp_path / "config.toml", None)
    )
    monkeypatch.setattr(llm_gptme, "_TOKEN_DIR", tmp_path / "auth")
    path = llm_gptme._get_token_path(llm_gptme._LEGACY_SERVICE_URLS[legacy_index])
    path.parent.mkdir()
    path.write_text(content)
    problems = detect_config_problems()
    assert [(p.name, p.path) for p in problems] == [("auth-cache-invalid", path)]
    assert path.read_text() == content


def test_cli_invalid_config_never_reports_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from gptme.cli.main import main
    from gptme.config import user

    original = CORRUPT_PROVIDERS_TOML + "broken = [\n"
    cfg = _make_config(tmp_path, original)
    monkeypatch.setattr(user, "get_user_config_paths", lambda: (cfg, None))
    result = CliRunner().invoke(main, ["--check-config", "--fix"])
    assert result.exit_code != 0
    assert "Fixed " not in result.output
    assert "config-invalid" in result.output or "could not repair" in result.output
    assert cfg.read_text() == original


@pytest.mark.parametrize(
    "extra",
    [
        'api_key = "token]#suffix"\n',
        'api_key = "token, name=secret" # "comment],"\n',
        'default_model = "model\\"quote"\n',
    ],
)
def test_repair_preserves_quoted_values(tmp_path: Path, extra: str) -> None:
    original = (
        'providers = [name = "local"\nbase_url = "http://localhost/v1"\n'
        + extra
        + "]\n"
    )
    cfg = _make_config(tmp_path, original)
    fix_providers_corruption(cfg)
    actual = tomllib.loads(cfg.read_text())["providers"]
    expected = tomllib.loads(
        'name = "local"\nbase_url = "http://localhost/v1"\n' + extra
    )
    assert actual == [expected]


def test_repair_multiple_providers(tmp_path: Path) -> None:
    original = (
        'providers = [name = "first"\nbase_url = "http://localhost/first"\n'
        ', name = "second"\nbase_url = "http://localhost/second"\n]\n'
    )
    cfg = _make_config(tmp_path, original)
    fix_providers_corruption(cfg)
    providers = tomllib.loads(cfg.read_text())["providers"]
    assert providers == [
        {"name": "first", "base_url": "http://localhost/first"},
        {"name": "second", "base_url": "http://localhost/second"},
    ]


@pytest.mark.parametrize(
    "original",
    [
        "[plugin]\n" + CORRUPT_PROVIDERS_TOML,
        'description = """\n' + CORRUPT_PROVIDERS_TOML + '"""\nbroken = [\n',
        CORRUPT_PROVIDERS_TOML.replace("base_url = ", "unknown = "),
        CORRUPT_PROVIDERS_TOML.replace("base_url = ", "base_url = INVALID\nother = "),
        CORRUPT_PROVIDERS_TOML.replace("base_url = ", "name = "),
    ],
)
def test_unrecognized_repair_is_non_destructive(tmp_path: Path, original: str) -> None:
    cfg = _make_config(tmp_path, original)
    with pytest.raises(ValueError, match="no safe providers repair"):
        fix_providers_corruption(cfg)
    assert cfg.read_text() == original
    assert not list(tmp_path.glob("*.bak"))


def test_default_cache_shadows_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.config import user
    from gptme.llm import llm_gptme

    monkeypatch.setattr(
        user, "get_user_config_paths", lambda: (tmp_path / "config.toml", None)
    )
    monkeypatch.setattr(llm_gptme, "_TOKEN_DIR", tmp_path / "auth")
    primary = llm_gptme._get_token_path()
    primary.parent.mkdir()
    primary.write_text('{"access_token": "fixture"}')
    legacy = llm_gptme._get_token_path(llm_gptme._LEGACY_SERVICE_URLS[0])
    legacy.write_text("null")
    assert detect_config_problems() == []
    # An explicit root requests an audit of all its cache files.
    assert [(p.name, p.path) for p in detect_config_problems(tmp_path)] == [
        ("auth-cache-invalid", legacy)
    ]


def test_cli_repair_success_and_read_only_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from gptme.cli.main import main
    from gptme.config import user
    from gptme.llm import llm_gptme

    original = CORRUPT_PROVIDERS_TOML + 'env.FOO = "bar"\n'
    cfg = _make_config(tmp_path, original)
    monkeypatch.setattr(user, "get_user_config_paths", lambda: (cfg, None))
    monkeypatch.setattr(llm_gptme, "_TOKEN_DIR", tmp_path / "auth")
    runner = CliRunner()
    check = runner.invoke(main, ["--check-config"])
    assert check.exit_code == 1
    assert "providers-not-array" in check.output
    assert cfg.read_text() == original
    assert not list(tmp_path.glob("*.bak"))
    fixed = runner.invoke(main, ["--check-config", "--fix"])
    assert fixed.exit_code == 0, fixed.output
    assert "Fixed " in fixed.output
    assert tomllib.loads(cfg.read_text())["env"] == {"FOO": "bar"}
    assert len(list(tmp_path.glob("*.bak"))) == 1
    assert list(tmp_path.glob("*.bak"))[0].read_text() == original


def test_cli_reports_repair_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from gptme.cli.main import main
    from gptme.config import user, validate
    from gptme.llm import llm_gptme

    cfg = _make_config(tmp_path, CORRUPT_PROVIDERS_TOML)
    monkeypatch.setattr(user, "get_user_config_paths", lambda: (cfg, None))
    monkeypatch.setattr(llm_gptme, "_TOKEN_DIR", tmp_path / "auth")

    def fail_repair(path: Path) -> Path:
        raise OSError("fixture write failure")

    monkeypatch.setattr(validate, "fix_providers_corruption", fail_repair)
    result = CliRunner().invoke(main, ["--check-config", "--fix"])
    assert result.exit_code == 2
    assert "could not repair" in result.output
    assert "Fixed " not in result.output
    assert cfg.read_text() == CORRUPT_PROVIDERS_TOML


def test_unterminated_comment_array_returns_promptly(tmp_path: Path) -> None:
    import subprocess
    import sys

    cfg = _make_config(tmp_path, "providers = [\n" + "# x\n" * 100)
    script = (
        "from pathlib import Path; from gptme.config.validate import detect_config_problems; "
        f"assert [p.name for p in detect_config_problems(Path({str(tmp_path)!r}))] == ['config-invalid']"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, timeout=5, check=False
    )
    assert result.returncode == 0, result.stderr.decode()
    assert cfg.read_text() == "providers = [\n" + "# x\n" * 100


def test_repair_replace_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    cfg = _make_config(tmp_path, CORRUPT_PROVIDERS_TOML)

    def fail_replace(src: object, dst: object) -> None:
        raise OSError("fixture publication failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="fixture publication failure"):
        fix_providers_corruption(cfg)
    assert cfg.read_text() == CORRUPT_PROVIDERS_TOML
    assert len(list(tmp_path.glob("*.bak"))) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [cfg.name, list(tmp_path.glob("*.bak"))[0].name]
    )


def test_backup_is_private_from_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    cfg = _make_config(tmp_path, CORRUPT_PROVIDERS_TOML)
    cfg.chmod(0o600)
    real_open = os.open
    seen: list[int] = []

    def inspect_open(
        path: object, flags: int, mode: int = 0o777, **kwargs: object
    ) -> int:
        fd = real_open(path, flags, mode, **kwargs)  # type: ignore[arg-type]
        if str(path).endswith(".bak"):
            seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return fd

    monkeypatch.setattr(os, "open", inspect_open)
    old_umask = os.umask(0o022)
    try:
        bak = fix_providers_corruption(cfg)
    finally:
        os.umask(old_umask)
    assert seen == [0o600]
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert bak.read_text() == CORRUPT_PROVIDERS_TOML


def test_intervening_edit_aborts_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    cfg = _make_config(tmp_path, CORRUPT_PROVIDERS_TOML)
    changed = 'env.MODEL = "new/model"\n'
    fsync = os.fsync

    def edit_during_write(fd: int) -> None:
        fsync(fd)
        cfg.write_text(changed)

    monkeypatch.setattr(os, "fsync", edit_during_write)
    with pytest.raises(ValueError, match="changed during repair"):
        fix_providers_corruption(cfg)
    assert cfg.read_text() == changed
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == CORRUPT_PROVIDERS_TOML
    assert len(list(tmp_path.iterdir())) == 2


def test_repair_preserves_symlink(tmp_path: Path) -> None:
    target = tmp_path / "managed.toml"
    target.write_text(CORRUPT_PROVIDERS_TOML)
    cfg = tmp_path / "config.toml"
    cfg.symlink_to(target)
    bak = fix_providers_corruption(cfg)
    assert cfg.is_symlink()
    assert cfg.resolve() == target
    assert (
        tomllib.loads(target.read_text())["providers"]
        == tomllib.loads(CLEAN_PROVIDERS_TOML)["providers"]
    )
    assert bak.read_text() == CORRUPT_PROVIDERS_TOML


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
@pytest.mark.parametrize("symlink", [False, True])
def test_repair_preserves_target_permissions(
    tmp_path: Path, mode: int, symlink: bool
) -> None:
    import stat

    target = _make_config(tmp_path, CORRUPT_PROVIDERS_TOML)
    target.chmod(mode)
    cfg = target
    if symlink:
        cfg = tmp_path / "linked.toml"
        cfg.symlink_to(target)

    bak = fix_providers_corruption(cfg)

    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert bak.read_text() == CORRUPT_PROVIDERS_TOML
    assert (
        tomllib.loads(target.read_text())["providers"]
        == tomllib.loads(CLEAN_PROVIDERS_TOML)["providers"]
    )
    assert cfg.is_symlink() == symlink


@pytest.mark.skipif(sys.platform != "linux", reason="Linux POSIX ACLs")
@pytest.mark.parametrize("existing_acl", [False, True])
def test_repair_preserves_access_acl(tmp_path: Path, existing_acl: bool) -> None:
    import errno
    import stat
    import struct

    cfg = _make_config(tmp_path, CORRUPT_PROVIDERS_TOML)
    cfg.chmod(0o640)

    def acl(named_user: int, owner: int) -> bytes:
        return struct.pack("<I", 2) + b"".join(
            struct.pack("<HHI", tag, perm, uid)
            for tag, perm, uid in [
                (1, owner, 0xFFFFFFFF),
                (2, 4, named_user),
                (4, 0, 0xFFFFFFFF),
                (16, 4, 0xFFFFFFFF),
                (32, 0, 0xFFFFFFFF),
            ]
        )

    try:
        if existing_acl:
            os.setxattr(cfg, "system.posix_acl_access", acl(os.getuid() + 1, 6))
        # The config predates the default ACL. Replacements inherit a different
        # named reader, which chmod(0640) alone would incorrectly enable.
        os.setxattr(tmp_path, "system.posix_acl_default", acl(os.getuid() + 2, 7))
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EPERM):
            pytest.skip(f"POSIX ACLs unavailable: {exc}")
        raise
    expected = os.getxattr(cfg, "system.posix_acl_access") if existing_acl else None

    bak = fix_providers_corruption(cfg)

    if expected is None:
        assert "system.posix_acl_access" not in os.listxattr(cfg)
    else:
        assert os.getxattr(cfg, "system.posix_acl_access") == expected
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o640
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert bak.read_text() == CORRUPT_PROVIDERS_TOML
    assert (
        tomllib.loads(cfg.read_text())["providers"]
        == tomllib.loads(CLEAN_PROVIDERS_TOML)["providers"]
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux POSIX ACLs")
def test_acl_preservation_failure_aborts_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(tmp_path, CORRUPT_PROVIDERS_TOML)

    def denied_acl(path: object, name: str) -> bytes:
        raise PermissionError("ACL read denied")

    monkeypatch.setattr(os, "getxattr", denied_acl)
    with pytest.raises(PermissionError, match="ACL read denied"):
        fix_providers_corruption(cfg)
    assert cfg.read_text() == CORRUPT_PROVIDERS_TOML
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == CORRUPT_PROVIDERS_TOML
    assert set(tmp_path.iterdir()) == {cfg, backups[0]}


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
@pytest.mark.parametrize("simulate_windows", [False, True])
def test_repair_writes_validated_newlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    line_ending: str,
    simulate_windows: bool,
) -> None:
    import tempfile
    from typing import Any

    # Force Windows' default text-write translation on any host. Explicit
    # newline handling must override it; also exercise the native platform.
    real_temporary_file = tempfile.NamedTemporaryFile

    def windows_temporary_file(**kwargs: Any) -> Any:
        if kwargs.get("newline") is None:
            kwargs["newline"] = "\r\n"
        return real_temporary_file(**kwargs)

    if simulate_windows:
        monkeypatch.setattr(tempfile, "NamedTemporaryFile", windows_temporary_file)

    original = (
        (UNRELATED_SETTING + CORRUPT_PROVIDERS_TOML + 'env.FOO = "bar"\n')
        .replace("\n", line_ending)
        .encode("utf-8")
    )
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(original)
    bak = fix_providers_corruption(cfg)
    repaired = cfg.read_bytes()
    # Parse raw bytes, not read_text(): universal newline decoding could hide
    # the doubled carriage returns produced by the broken Windows writer.
    parsed = tomllib.loads(repaired.decode("utf-8"))
    assert parsed["model"] == "openai/gpt-4o-mini"
    assert parsed["env"] == {"FOO": "bar"}
    assert parsed["providers"] == tomllib.loads(CLEAN_PROVIDERS_TOML)["providers"]
    assert b"\r\r\n" not in repaired
    assert bak.read_bytes() == original
    assert detect_config_problems(tmp_path) == []
