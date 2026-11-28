"""Config writes are atomic, private for secrets, and safe under concurrency.

Regression tests for gptme/gptme#5128.
"""

import errno
import logging
import multiprocessing
import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import tomlkit

from gptme.config import user as user_mod
from gptme.config.models import ProviderConfig
from gptme.config.user import (
    load_user_config,
    save_provider_config,
    set_config_value,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
# Evaluated at import time by ``skipif`` below, so it must not touch
# ``os.geteuid`` on Windows (where that attribute does not exist).
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gptme" / "config.toml"
    path.parent.mkdir()
    path.write_text('[env]\nEXISTING = "keep"\n', encoding="utf-8")
    monkeypatch.setattr(user_mod, "config_path", str(path))
    return path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@posix_only
def test_new_local_config_is_owner_only(config_file: Path) -> None:
    old_umask = os.umask(0o022)
    try:
        set_config_value("env.OPENROUTER_API_KEY", "sk-or-x", reload=False, local=True)
    finally:
        os.umask(old_umask)

    local = config_file.with_name("config.local.toml")
    assert _mode(local) == 0o600
    assert "sk-or-x" in local.read_text(encoding="utf-8")


@posix_only
def test_existing_local_config_is_tightened(config_file: Path) -> None:
    local = config_file.with_name("config.local.toml")
    local.write_text('[env]\nOLD = "1"\n', encoding="utf-8")
    local.chmod(0o644)

    save_provider_config(
        ProviderConfig(name="p", base_url="https://x.invalid", api_key="secret"),
        reload=False,
        local=True,
    )

    assert _mode(local) == 0o600
    doc = tomlkit.parse(local.read_text(encoding="utf-8"))
    assert doc["env"]["OLD"] == "1"
    assert doc["providers"][0]["api_key"] == "secret"


@posix_only
def test_main_config_keeps_its_mode(config_file: Path) -> None:
    config_file.chmod(0o640)
    set_config_value("models.default", "openai/gpt-x", reload=False)
    assert _mode(config_file) == 0o640


@posix_only
@pytest.mark.parametrize("deny_chown", [False, True])
def test_main_config_keeps_its_group(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, deny_chown: bool
) -> None:
    groups = [gid for gid in os.getgroups() if gid != config_file.stat().st_gid]
    if not groups:
        pytest.skip("requires membership in a second group")
    original = config_file.read_bytes()
    gid = groups[0]
    os.chown(config_file, -1, gid)
    config_file.chmod(0o640)
    real_fchmod = os.fchmod

    def check_group_before_granting_access(fd: int, mode: int) -> None:
        if mode & 0o070:
            assert os.fstat(fd).st_gid == gid
        real_fchmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", check_group_before_granting_access)
    if deny_chown:

        def denied(*args: Any) -> None:
            raise PermissionError("cannot preserve config group")

        monkeypatch.setattr(os, "fchown", denied)
        with pytest.raises(PermissionError, match="cannot preserve config group"):
            set_config_value("models.default", "openai/new", reload=False)
        assert config_file.read_bytes() == original
    else:
        set_config_value("models.default", "openai/new", reload=False)
        assert load_user_config(str(config_file)).models.default == "openai/new"
    assert config_file.stat().st_gid == gid
    assert _mode(config_file) == 0o640
    assert not list(config_file.parent.glob("*.tmp"))


@posix_only
def test_owner_only_config_does_not_need_chown(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 0600 save must not attempt a group change it cannot perform.

    Regression: the group was preserved whenever it differed, regardless of the
    final mode. An unprivileged user cannot chown to a group they are not in, so
    a 0600 config restored from another machine (or a bind mount with a foreign
    gid) failed to save with PermissionError even though group access was never
    granted.
    """
    groups = [gid for gid in os.getgroups() if gid != config_file.stat().st_gid]
    if not groups:
        pytest.skip("requires membership in a second group")
    os.chown(config_file, -1, groups[0])
    config_file.chmod(0o600)

    def denied(*args: Any) -> None:
        raise PermissionError("cannot preserve config group")

    monkeypatch.setattr(os, "fchown", denied)
    set_config_value("models.default", "openai/private", reload=False)

    assert load_user_config(str(config_file)).models.default == "openai/private"
    assert _mode(config_file) == 0o600


def test_failed_write_leaves_original_intact(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = config_file.with_name("config.local.toml")
    original = '[env]\nANTHROPIC_API_KEY = "sk-ant-keep"\n'
    local.write_text(original, encoding="utf-8")

    def fail(_fd: int) -> None:
        raise OSError(27, "File too large")

    monkeypatch.setattr(user_mod.os, "fsync", fail)
    with pytest.raises(OSError, match="File too large"):
        set_config_value("env.OTHER", "x" * 10_000, reload=False, local=True)

    assert local.read_text(encoding="utf-8") == original
    # No staging file is left behind next to the config.
    leftovers = sorted(
        p.name for p in config_file.parent.iterdir() if p.name.endswith(".tmp")
    )
    assert leftovers == []


def test_write_through_symlink_updates_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "config.toml"
    target.write_text("[env]\n", encoding="utf-8")
    link_dir = tmp_path / "home"
    link_dir.mkdir()
    link = link_dir / "config.toml"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(user_mod, "config_path", str(link))

    set_config_value("models.default", "anthropic/claude-x", reload=False)

    assert link.is_symlink()
    assert tomlkit.parse(target.read_text())["models"]["default"] == (
        "anthropic/claude-x"
    )


def _set_key(path: str, i: int) -> None:
    user_mod.config_path = path
    set_config_value(f"env.KEY_{i}", str(i), reload=False, local=True)


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs fork"
)
def test_concurrent_writers_do_not_lose_updates(config_file: Path) -> None:
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(target=_set_key, args=(str(config_file), i)) for i in range(32)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    local = config_file.with_name("config.local.toml")
    env = tomlkit.parse(local.read_text(encoding="utf-8"))["env"]
    assert sorted(env) == sorted(f"KEY_{i}" for i in range(32))


def test_onboard_saves_model_where_gptme_reads_it(config_file: Path) -> None:
    from gptme.cli import onboard

    model = 'openai/odd"name'
    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            return_value=SimpleNamespace(
                user=SimpleNamespace(models=SimpleNamespace(default=None), env={})
            ),
        ),
        patch("gptme.config.core.reload_config"),
    ):
        onboard._create_config("openai", model)

    config = load_user_config(str(config_file))
    assert config.models.default == model
    # The rest of the existing config survives.
    assert config.env["EXISTING"] == "keep"
    assert "chat" not in tomlkit.parse(config_file.read_text(encoding="utf-8"))


def test_onboard_keeps_existing_default_when_declined(config_file: Path) -> None:
    from gptme.cli import onboard

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            return_value=SimpleNamespace(
                user=SimpleNamespace(
                    models=SimpleNamespace(default="anthropic/x"), env={}
                )
            ),
        ),
        patch.object(onboard.Confirm, "ask", return_value=False),
        patch.object(onboard, "set_config_value") as save,
    ):
        onboard._create_config("openai", "openai/y")

    save.assert_not_called()


@pytest.mark.parametrize("accept", [False, True])
def test_onboard_summary_only_claims_a_saved_model(
    config_file: Path, capsys: pytest.CaptureFixture[str], accept: bool
) -> None:
    from gptme.cli import onboard

    config_file.write_text('[models]\ndefault = "anthropic/old"\n', encoding="utf-8")
    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard, "_detect_providers", return_value={"openai": (True, "key")}
        ),
        patch.object(onboard, "_select_provider", return_value="openai"),
        patch.object(onboard, "_test_provider", return_value=(True, None)),
        patch.object(onboard.Prompt, "ask", return_value="openai/new"),
        patch.object(onboard.Confirm, "ask", return_value=accept),
        patch.object(
            onboard,
            "get_config",
            side_effect=lambda: SimpleNamespace(
                user=load_user_config(str(config_file))
            ),
        ),
        patch("gptme.config.core.reload_config"),
    ):
        result = onboard._run_wizard()

    output = capsys.readouterr().out
    if accept:
        assert result == 0
        assert "Setup complete!" in output
        assert "Model: openai/new" in output
        assert load_user_config(str(config_file)).models.default == "openai/new"
    else:
        assert result == 1
        assert "Setup complete!" not in output
        assert "Model: openai/new" not in output
        assert "Configuration was not changed" in output
        assert load_user_config(str(config_file)).models.default == "anthropic/old"


@posix_only
def test_symlinked_local_config_with_other_target_name_is_private(
    config_file: Path,
) -> None:
    target = config_file.parent / "secrets.toml"
    target.write_text("[env]\n", encoding="utf-8")
    target.chmod(0o644)
    config_file.with_name("config.local.toml").symlink_to(target)

    set_config_value("env.KEY", "secret", reload=False, local=True)

    assert _mode(target) == 0o600


def test_first_run_create_does_not_replace_concurrent_save(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[models]\ndefault = "theirs"\n', encoding="utf-8")
    assert not user_mod._write_config_text(path, "[env]\n", exclusive=True)
    assert "theirs" in path.read_text(encoding="utf-8")
    # A lock sidecar is expected; no staged or placeholder file is left.
    left = [p.name for p in tmp_path.iterdir() if not p.name.endswith(".lock")]
    assert left == ["config.toml"]


def test_onboard_updates_local_default_when_it_wins(config_file: Path) -> None:
    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    local.write_text('[models]\ndefault = "anthropic/old"\n', encoding="utf-8")
    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            return_value=SimpleNamespace(
                user=SimpleNamespace(
                    models=SimpleNamespace(default="anthropic/old"), env={}
                )
            ),
        ),
        patch.object(onboard.Confirm, "ask", return_value=True),
        patch("gptme.config.core.reload_config"),
    ):
        onboard._create_config("openai", "openai/new")

    assert load_user_config(str(config_file)).models.default == "openai/new"


def test_onboard_reads_legacy_encoded_local_config(config_file: Path) -> None:
    """A config.local.toml gptme can load (non-UTF-8) must not crash onboarding."""
    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    local.write_bytes('# café\n[models]\ndefault = "anthropic/old"\n'.encode("cp1252"))
    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            return_value=SimpleNamespace(
                user=SimpleNamespace(
                    models=SimpleNamespace(default="anthropic/old"), env={}
                )
            ),
        ),
        patch.object(onboard.Confirm, "ask", return_value=True),
        patch("gptme.config.core.reload_config"),
    ):
        assert onboard._create_config("openai", "openai/new") == local

    assert load_user_config(str(config_file)).models.default == "openai/new"


@pytest.mark.parametrize("existing", [False, True])
def test_first_run_create_without_hard_links(tmp_path: Path, existing: bool) -> None:
    """Exclusive create still works (and still refuses to replace) without os.link."""
    path = tmp_path / "config.toml"
    if existing:
        path.write_text('[models]\ndefault = "theirs"\n', encoding="utf-8")

    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")

    with patch.object(user_mod.os, "link", no_links):
        created = user_mod._write_config_text(path, "[env]\n", exclusive=True)

    assert created is not existing
    expected = "theirs" if existing else "[env]"
    assert expected in path.read_text(encoding="utf-8")
    # A lock sidecar is expected; no staged or placeholder file is left.
    left = [p.name for p in tmp_path.iterdir() if not p.name.endswith(".lock")]
    assert left == ["config.toml"]


@posix_only
def test_reencoding_backup_is_never_wider_than_the_original(
    config_file: Path,
) -> None:
    """The .orig backup of a legacy-encoded local config stays owner-only."""
    local = config_file.with_name("config.local.toml")
    original = '[env]\nKEY = "café"\n'.encode("cp1252")
    local.write_bytes(original)
    local.chmod(0o600)
    load_user_config(str(config_file))  # marks the guessed decoding

    old_umask = os.umask(0o022)
    try:
        set_config_value("models.default", "openai/new", reload=False, local=True)
    finally:
        os.umask(old_umask)

    backup = local.with_name("config.local.toml.orig")
    assert _mode(backup) == 0o600
    assert backup.read_bytes() == original


def test_first_run_create_failed_publish_leaves_no_placeholder(
    tmp_path: Path,
) -> None:
    """If publishing fails without hard links, no empty config is left behind."""
    path = tmp_path / "config.toml"

    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")

    def failing_replace(src, dst):
        raise OSError(5, "Input/output error")

    with (
        patch.object(user_mod.os, "link", no_links),
        patch.object(user_mod.os, "replace", failing_replace),
        pytest.raises(OSError, match="Input/output error"),
    ):
        user_mod._write_config_text(path, "[env]\n", exclusive=True)

    assert [p for p in tmp_path.iterdir() if not p.name.endswith(".lock")] == []


def test_first_run_cleanup_holds_the_write_lock(tmp_path: Path) -> None:
    """A concurrent save cannot land between the cleanup check and the unlink."""
    import threading

    path = tmp_path / "config.toml"
    other_writer_got_lock = threading.Event()
    writers: list[threading.Thread] = []

    def other_writer() -> None:
        with user_mod._config_write_lock(path):
            other_writer_got_lock.set()

    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")

    def replace_fails_while_another_writer_waits(src, dst):
        writer = threading.Thread(target=other_writer)
        writers.append(writer)
        writer.start()
        assert not other_writer_got_lock.wait(0.3)
        raise OSError(5, "Input/output error")

    try:
        with (
            patch.object(user_mod.os, "link", no_links),
            patch.object(
                user_mod.os, "replace", replace_fails_while_another_writer_waits
            ),
            pytest.raises(OSError, match="Input/output error"),
        ):
            user_mod._write_config_text(path, "[env]\n", exclusive=True)

        assert other_writer_got_lock.wait(5)
        assert not path.exists()
    finally:
        for writer in writers:
            writer.join(5)
    assert not any(writer.is_alive() for writer in writers)


def test_config_write_lock_is_reentrant_in_one_thread(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    with user_mod._config_write_lock(path), user_mod._config_write_lock(path):
        pass


def _read_only_lock_takes_ebadf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model NFS, where flock emulation needs a writable descriptor."""
    if user_mod.fcntl is None:
        pytest.skip("posix flock only")
    real_open = Path.open

    def lock_writable_only(self: Path, mode: str = "r", *args: Any, **kwargs: Any):
        if self.name.endswith(".lock") and mode == "a+b":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_open(self, mode, *args, **kwargs)

    real_flock = user_mod.fcntl.flock

    def nfs_flock(fd: int, operation: int) -> None:
        if operation == user_mod.fcntl.LOCK_EX:
            flags = user_mod.fcntl.fcntl(fd, user_mod.fcntl.F_GETFL)
            if flags & os.O_ACCMODE == os.O_RDONLY:
                raise OSError(errno.EBADF, "Bad file descriptor")
        real_flock(fd, operation)

    monkeypatch.setattr(Path, "open", lock_writable_only)
    monkeypatch.setattr(user_mod.fcntl, "flock", nfs_flock)


def test_read_only_lock_fallback_degrades_on_nfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An NFS home rejects the read-only fallback's LOCK_EX with EBADF.

    Linux emulates flock over NFS with fcntl byte-range locks, where an
    exclusive lock needs a descriptor opened for writing. The fallback then
    fails on exactly the shared, group-writable home it targets; the save must
    proceed unlocked with a warning instead of raising EBADF every time.
    """
    _read_only_lock_takes_ebadf(monkeypatch)
    path = tmp_path / "config.toml"
    # The other user's sidecar exists but is not writable for us.
    (tmp_path / ".config.toml.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(user_mod, "config_path", str(path))

    with caplog.at_level(logging.WARNING):
        set_config_value("user.name", "new", reload=False)

    assert "without it" in caplog.text
    assert 'name = "new"' in path.read_text(encoding="utf-8")


def test_missing_lock_in_unwritable_directory_does_not_fail_the_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No sidecar and an unwritable parent: warn, do not name the lock file."""
    if user_mod.fcntl is None:
        pytest.skip("posix flock only")
    real_open = Path.open

    def lock_unwritable(self: Path, mode: str = "r", *args: Any, **kwargs: Any):
        if self.name.endswith(".lock"):
            if mode == "a+b":
                raise PermissionError(errno.EACCES, "Permission denied", str(self))
            raise FileNotFoundError(errno.ENOENT, "No such file", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", lock_unwritable)
    path = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(path))

    with caplog.at_level(logging.WARNING):
        set_config_value("user.name", "new", reload=False)

    assert "Cannot create lock file" in caplog.text
    assert 'name = "new"' in path.read_text(encoding="utf-8")


def test_initial_config_data_is_sparse_for_local_and_runtime_layers(
    tmp_path: Path,
) -> None:
    """Only the main config may be seeded with built-in defaults.

    The local layer and an operator-supplied runtime layer start empty: writing
    defaults into either would shadow the layer that is supposed to win.
    """
    main = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"

    assert user_mod._initial_config_data(str(main))["user"]["name"] == "User"
    assert user_mod._initial_config_data(str(local)) == {}
    user_mod.get_user_config_runtime_path(str(main)).write_text(
        '[user]\nabout = "runtime"\n', encoding="utf-8"
    )
    assert user_mod._initial_config_data(str(main)) == {}


@posix_only
@pytest.mark.parametrize("final_mode", [0o600, 0o640, 0o644])
def test_staging_file_is_owner_only_until_written(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, final_mode: int
) -> None:
    """The staged copy is never more open than 0600 while it gets the contents.

    Another user who opens a wider staging file keeps that descriptor after a
    later chmod, and would read the config (which may hold API keys) through it.
    """
    config_file.chmod(final_mode)
    real_open = os.open
    real_fchmod = os.fchmod
    staged_fds: set[int] = set()
    modes_at_create: list[int] = []
    # (mode, staged size) for every chmod of the staging file
    chmods: list[tuple[int, int]] = []

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        fd = real_open(path, flags, mode, *args, **kwargs)
        if str(path).endswith(".tmp"):
            staged_fds.add(fd)
            modes_at_create.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return fd

    def recording_fchmod(fd: int, mode: int) -> None:
        if fd in staged_fds:
            chmods.append((mode, os.fstat(fd).st_size))
        real_fchmod(fd, mode)

    monkeypatch.setattr(user_mod.os, "open", recording_open)
    monkeypatch.setattr(user_mod.os, "fchmod", recording_fchmod)
    old_umask = os.umask(0o022)
    try:
        set_config_value("env.OPENAI_API_KEY", "sk-secret", reload=False)
    finally:
        os.umask(old_umask)

    assert modes_at_create, "staging file was not observed"
    assert all(m & 0o077 == 0 for m in modes_at_create), [
        oct(m) for m in modes_at_create
    ]
    # Group/other bits are only granted once the complete contents are in place.
    final_size = config_file.stat().st_size
    assert all(size == final_size for mode, size in chmods if mode & 0o077), chmods
    assert _mode(config_file) == final_mode
    assert "sk-secret" in config_file.read_text(encoding="utf-8")


class _FakeMsvcrt:
    """Models msvcrt.locking: LK_LOCK gives up after 10 one-second retries."""

    LK_LOCK, LK_NBLCK, LK_UNLCK = 1, 2, 0

    def __init__(self, busy_attempts: int, fail_errno: int = 13) -> None:
        self.busy_attempts = busy_attempts
        self.fail_errno = fail_errno
        self.locked = False

    def _try(self) -> bool:
        if self.busy_attempts > 0:
            self.busy_attempts -= 1
            return False
        self.locked = True
        return True

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        if mode == self.LK_UNLCK:
            self.locked = False
        elif mode == self.LK_NBLCK:
            if not self._try():
                raise OSError(self.fail_errno, "Permission denied")
        elif mode == self.LK_LOCK:
            if not any(self._try() for _ in range(10)):
                raise OSError(36, "Resource deadlock avoided")


def test_windows_config_write_lock_waits_for_a_long_held_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writers wait for the lock on Windows too, not give up after ~10s."""

    fake = _FakeMsvcrt(busy_attempts=15, fail_errno=errno.EACCES)
    monkeypatch.setattr(user_mod, "fcntl", None)
    monkeypatch.setattr(user_mod, "msvcrt", fake)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with user_mod._config_write_lock(tmp_path / "config.toml"):
        assert fake.locked
    assert not fake.locked
    assert fake.busy_attempts == 0


def test_windows_config_write_lock_reraises_other_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeMsvcrt(busy_attempts=1, fail_errno=errno.EBADF)
    monkeypatch.setattr(user_mod, "fcntl", None)
    monkeypatch.setattr(user_mod, "msvcrt", fake)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with (
        pytest.raises(OSError, match="Permission denied") as excinfo,
        user_mod._config_write_lock(tmp_path / "config.toml"),
    ):
        pass
    assert excinfo.value.errno == errno.EBADF


def test_replace_retries_transient_permission_error(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows sharing violation (reader/AV) must not fail the save."""
    real_replace = os.replace
    calls = 0

    def flaky_replace(src, dst):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise PermissionError(13, "Permission denied")
        return real_replace(src, dst)

    monkeypatch.setattr(user_mod, "_replace_retries_transient_errors", True)
    monkeypatch.setattr(user_mod.os, "replace", flaky_replace)
    monkeypatch.setattr(user_mod.time, "sleep", lambda _s: None)

    set_config_value("models.default", "openai/retried", reload=False)

    assert calls == 3
    assert load_user_config(str(config_file)).models.default == "openai/retried"


def test_replace_raises_clear_error_after_exhausting_retries(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanently locked config surfaces a clear error, not a bare traceback."""
    original = config_file.read_text(encoding="utf-8")

    def always_locked(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(user_mod, "_replace_retries_transient_errors", True)
    monkeypatch.setattr(user_mod.os, "replace", always_locked)
    monkeypatch.setattr(user_mod.time, "sleep", lambda _s: None)

    with pytest.raises(OSError, match="remained locked by another process"):
        set_config_value("models.default", "openai/x", reload=False)

    # The old file is intact and no staging file is left behind.
    assert config_file.read_text(encoding="utf-8") == original
    leftovers = sorted(
        p.name for p in config_file.parent.iterdir() if p.name.endswith(".tmp")
    )
    assert leftovers == []


@posix_only
def test_replace_permission_error_is_not_retried_on_posix(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On POSIX a refused rename is deterministic, so it must not be retried.

    Retrying delayed the error by ~0.75s and then re-raised it as a bare OSError
    claiming a nonexistent lock, hiding the errno from callers that handle
    permission failures.
    """
    calls = 0

    def refused(src, dst):
        nonlocal calls
        calls += 1
        raise PermissionError(errno.EACCES, "Permission denied", str(dst))

    monkeypatch.setattr(user_mod.os, "replace", refused)
    monkeypatch.setattr(user_mod.time, "sleep", lambda _s: None)

    with pytest.raises(PermissionError) as excinfo:
        set_config_value("models.default", "openai/x", reload=False)

    assert calls == 1
    assert excinfo.value.errno == errno.EACCES


@posix_only
def test_group_readable_config_does_not_need_chown(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 0644 config needs no fchown: the group has only what 'other' has.

    Regression: group preservation ran whenever any group bit was set, so a
    0644 config restored with ``rsync -a`` (or a bind mount with an unmapped
    gid) failed every save with EPERM from ``fchown`` -- where the old in-place
    write succeeded, because preserving that group protects nothing.
    """
    groups = [gid for gid in os.getgroups() if gid != config_file.stat().st_gid]
    if not groups:
        pytest.skip("requires membership in a second group")
    os.chown(config_file, -1, groups[0])
    config_file.chmod(0o644)

    def denied(*args: Any) -> None:
        raise PermissionError("cannot preserve config group")

    monkeypatch.setattr(os, "fchown", denied)
    set_config_value("models.default", "openai/shared", reload=False)

    assert load_user_config(str(config_file)).models.default == "openai/shared"
    assert _mode(config_file) == 0o644


def test_onboard_offers_to_overwrite_corrupt_config(config_file: Path) -> None:
    """A corrupt config must keep the overwrite recovery path, not crash."""
    from gptme.cli import onboard

    corrupt = '[models\ndefault = "broken"\n'
    config_file.write_text(corrupt, encoding="utf-8")

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            side_effect=tomlkit.exceptions.TOMLKitError("corrupt"),
        ),
        patch.object(onboard.Confirm, "ask", return_value=True) as ask,
        patch("gptme.config.core.reload_config"),
    ):
        result = onboard._create_config("openai", "openai/new")

    assert result == config_file
    ask.assert_called_once()
    assert "Back up these files and continue?" in ask.call_args.args[0]
    backups = list(config_file.parent.glob("config.toml.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == corrupt
    assert load_user_config(str(config_file)).models.default == "openai/new"


@pytest.mark.parametrize(
    "corrupt_names",
    [("config.local.toml",), ("config.toml",), ("config.toml", "config.local.toml")],
)
@pytest.mark.parametrize("accept", [False, True])
def test_onboard_identifies_corrupt_files_before_recovery(
    config_file: Path,
    corrupt_names: tuple[str, ...],
    accept: bool,
) -> None:
    from io import StringIO

    from rich.console import Console

    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    local.write_text('[env]\nLOCAL_SETTING = "keep"\n', encoding="utf-8")
    corrupt = '[models\ndefault = "broken"\n'
    paths = (config_file, local)
    for path in paths:
        if path.name in corrupt_names:
            path.write_text(corrupt, encoding="utf-8")
    original = {path: path.read_bytes() for path in paths}
    output = StringIO()

    def confirm(prompt: str, **kwargs: Any) -> bool:
        before_confirmation = output.getvalue()
        for path in paths:
            assert (str(path) in before_confirmation) == (path.name in corrupt_names)
            assert path.read_bytes() == original[path]
        assert "back" in prompt.lower()
        return accept

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(onboard, "console", Console(file=output, width=1000)),
        patch.object(
            onboard,
            "get_config",
            side_effect=lambda: SimpleNamespace(
                user=load_user_config(str(config_file))
            ),
        ),
        patch.object(onboard.Confirm, "ask", side_effect=confirm),
        patch("gptme.config.core.reload_config"),
    ):
        onboard._create_config("openai", "openai/new")

    for path in paths:
        backups = list(path.parent.glob(f"{path.name}.corrupt-*"))
        if accept and path.name in corrupt_names:
            assert len(backups) == 1
            assert backups[0].read_bytes() == original[path]
        else:
            assert backups == []
            # The main file also receives the selected model on acceptance.
            if not accept or path == local:
                assert path.read_bytes() == original[path]
    if accept:
        assert load_user_config(str(config_file)).models.default == "openai/new"
        if "config.toml" not in corrupt_names:
            assert load_user_config(str(config_file)).env["EXISTING"] == "keep"


def test_onboard_declining_corrupt_overwrite_writes_nothing(
    config_file: Path,
) -> None:
    from gptme.cli import onboard

    corrupt = '[models\ndefault = "broken"\n'
    config_file.write_text(corrupt, encoding="utf-8")

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            side_effect=tomlkit.exceptions.TOMLKitError("corrupt"),
        ),
        patch.object(onboard.Confirm, "ask", return_value=False),
        patch.object(onboard, "set_config_value") as save,
    ):
        assert onboard._create_config("openai", "openai/new") is None

    save.assert_not_called()
    assert config_file.read_text(encoding="utf-8") == corrupt


def test_onboard_recovery_asks_before_replacing_a_surviving_default(
    config_file: Path,
) -> None:
    """Recovery must not silently overwrite a default in a valid local config.

    Only the corrupt main file was agreed to be moved aside; the surviving
    ``config.local.toml`` default goes through the normal replacement prompt.
    """
    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    local_default = '[models]\ndefault = "anthropic/old"\n'
    local.write_text(local_default, encoding="utf-8")
    config_file.write_text('[models\ndefault = "broken"\n', encoding="utf-8")

    asked: list[str] = []
    reads = {"count": 0}

    def confirm(prompt: str, **kwargs: Any) -> bool:
        asked.append(prompt)
        return "Back up" in prompt

    def read_config() -> Any:
        reads["count"] += 1
        if reads["count"] == 1:
            raise tomlkit.exceptions.TOMLKitError("corrupt")
        # After recovery the corrupt main file is gone, so this is the merged
        # view of what survived: the local default.
        return SimpleNamespace(user=load_user_config(str(config_file)))

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(onboard, "get_config", side_effect=read_config),
        patch.object(onboard.Confirm, "ask", side_effect=confirm),
        patch("gptme.config.core.reload_config"),
    ):
        assert onboard._create_config("openai", "openai/new") is None

    assert any("Replace it with" in prompt for prompt in asked)
    assert local.read_text(encoding="utf-8") == local_default
    assert len(list(config_file.parent.glob("config.toml.corrupt-*"))) == 1


def _hold_config_lock(path: str, ready: Any, release: Any) -> None:
    with user_mod._config_write_lock(path):
        ready.set()
        release.wait(30)


def _acquire_config_lock(path: str, acquired: Any) -> None:
    with user_mod._config_write_lock(path):
        acquired.set()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows file locking (msvcrt); spawn only exercised there",
)
def test_windows_spawned_writer_waits_for_the_lock(tmp_path: Path) -> None:
    """One spawn-based writer waits while another process holds the lock.

    The fake-msvcrt test and the fork-based concurrency test both skip Windows,
    so this is the only test that exercises the real Windows lock across
    processes. It is skipped on non-Windows hosts.
    """
    ctx = multiprocessing.get_context("spawn")
    path = str(tmp_path / "config.toml")
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(target=_hold_config_lock, args=(path, ready, release))
    holder.start()
    waiter = None
    try:
        assert ready.wait(30), "lock holder did not start"
        acquired = ctx.Event()
        waiter = ctx.Process(target=_acquire_config_lock, args=(path, acquired))
        waiter.start()
        assert not acquired.wait(1.0), "second writer did not wait for the lock"
        release.set()
        assert acquired.wait(30), "second writer never got the lock"
        waiter.join(30)
        assert waiter.exitcode == 0
    finally:
        release.set()
        holder.join(30)
        if waiter is not None:
            waiter.join(30)
        assert holder.exitcode == 0


def test_onboard_corrupt_symlink_backs_up_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dotfiles symlink must keep pointing at a file, not become a plain file."""
    from gptme.cli import onboard

    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text('[models\ndefault = "broken"\n', encoding="utf-8")
    link = tmp_path / "gptme" / "config.toml"
    link.parent.mkdir()
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(user_mod, "config_path", str(link))
    monkeypatch.setattr(onboard, "config_path", str(link))

    with (
        patch.object(
            onboard,
            "get_config",
            side_effect=tomlkit.exceptions.TOMLKitError("corrupt"),
        ),
        patch.object(onboard.Confirm, "ask", return_value=True),
        patch("gptme.config.core.reload_config"),
    ):
        assert onboard._create_config("openai", "openai/new") == link

    assert link.is_symlink()
    assert load_user_config(str(link)).models.default == "openai/new"
    backups = list(real.parent.glob("config.toml.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '[models\ndefault = "broken"\n'


@pytest.mark.parametrize("local", [False, True])
def test_read_only_config_is_not_replaced(config_file: Path, local: bool) -> None:
    path = config_file.with_name("config.local.toml") if local else config_file
    original = '[env]\nKEEP = "unchanged"\n'
    path.write_text(original, encoding="utf-8")
    path.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            set_config_value("models.default", "openai/new", reload=False, local=local)
        assert path.read_text(encoding="utf-8") == original
        assert list(path.parent.glob("*.tmp")) == []
    finally:
        path.chmod(0o600)


def test_config_the_current_user_cannot_write_is_not_replaced(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config owned by someone else must not be replaced by rename.

    In a group-writable directory the rename is authorized by the directory, so
    the mode's owner write bit alone would let another user replace a config
    they cannot write. The old in-place write raised ``PermissionError``.
    """
    original = config_file.read_text(encoding="utf-8")
    monkeypatch.setattr(user_mod.os, "access", lambda *args, **kwargs: False)

    with pytest.raises(PermissionError):
        set_config_value("models.default", "openai/new", reload=False)

    assert config_file.read_text(encoding="utf-8") == original


def test_recovery_locks_each_config_before_rechecking(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local file must use the same lock as a concurrent local save."""
    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    paths = (config_file, local)
    corrupt = '[models\ndefault = "broken"\n'
    for path in paths:
        path.write_text(corrupt, encoding="utf-8")
    real_load = onboard._load_config_doc
    checked = []

    def check_under_lock(path: str):
        held = user_mod._held_config_locks.__dict__.get("paths", set())
        assert str(Path(path).resolve()) in held
        checked.append(Path(path))
        return real_load(path)

    monkeypatch.setattr(onboard, "_load_config_doc", check_under_lock)
    onboard._back_up_corrupt_configs(list(paths))
    assert checked == list(paths)
    for path in paths:
        backups = list(path.parent.glob(f"{path.name}.corrupt-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == corrupt


def test_exclusive_create_leaves_existing_read_only_config(config_file: Path) -> None:
    original = config_file.read_bytes()
    config_file.chmod(0o444)
    try:
        assert not user_mod._write_config_text(config_file, "[env]\n", exclusive=True)
        assert config_file.read_bytes() == original
    finally:
        config_file.chmod(0o600)


def test_bind_mount_replace_failure_does_not_fall_back_to_truncation(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = config_file.read_bytes()

    def busy_mount(src: str | Path, dst: str | Path) -> None:
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(user_mod.os, "replace", busy_mount)
    with pytest.raises(OSError, match="Device or resource busy") as exc:
        set_config_value("models.default", "openai/new", reload=False)
    assert exc.value.errno == errno.EBUSY
    assert config_file.read_bytes() == original
    assert list(config_file.parent.glob("*.tmp")) == []


@posix_only
@pytest.mark.skipif(IS_ROOT, reason="root bypasses the permission check this exercises")
def test_lock_created_by_another_umask_still_allows_saving(config_file: Path) -> None:
    """A read-only sidecar must not block a writer that may still write the config.

    Two users sharing a group-writable config can each create the lock under
    their own umask; the lock only needs to be openable, not writable, because
    flock(2) takes LOCK_EX on a read-only descriptor.

    As non-root this exercises the fallback branch: the sidecar is 0400, so the
    primary ``open("a+b")`` raises ``PermissionError`` and the read-only
    descriptor is used instead. The assertion is that the save still lands; that
    the lock actually serialized is not observable from here.
    """
    lock_path = config_file.with_name(".config.toml.lock")
    lock_path.touch()
    lock_path.chmod(0o400)

    set_config_value("env.ADDED", "1", reload=False)

    assert 'ADDED = "1"' in config_file.read_text(encoding="utf-8")
    # The other user's sidecar is left exactly as it was.
    assert _mode(lock_path) == 0o400


def test_onboard_refuses_when_local_models_is_not_a_table(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A scalar `models` in config.local.toml shadows the main default.

    Writing the chosen model to config.toml would be discarded on reload, so
    onboarding must say so instead of reporting a successful save.
    """
    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    local.write_text('models = "oops"\n', encoding="utf-8")

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            return_value=SimpleNamespace(
                user=SimpleNamespace(models=SimpleNamespace(default=None), env={})
            ),
        ),
        patch.object(onboard, "set_config_value") as save,
    ):
        result = onboard._create_config("openai", "openai/y")

    save.assert_not_called()
    assert result is None
    out = capsys.readouterr().out.replace("\n", "")
    assert "not a table" in out
    assert "config.local.toml" in out
    # The local file is left alone: its retained settings must survive.
    assert local.read_text(encoding="utf-8") == 'models = "oops"\n'


def test_onboard_refuses_local_non_table_models_when_local_holds_the_default(
    config_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local file is the write target, so its own check cannot be skipped.

    With ``[env].MODEL`` in config.local.toml the effective default is local,
    which used to skip both non-table ``models`` checks. The writer then raised
    ``ValueError: ... 'models' exists but is not a table`` after the user had
    already confirmed the replacement.
    """
    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    original = 'models = "oops"\n\n[env]\nMODEL = "anthropic/old"\n'
    local.write_text(original, encoding="utf-8")
    # A process MODEL would outrank the user config and make the default
    # non-local, hiding the branch under test.
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("GPTME_MODEL", raising=False)

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            side_effect=lambda: SimpleNamespace(user=load_user_config()),
        ),
        patch.object(onboard, "set_config_value") as save,
    ):
        result = onboard._create_config("openai", "openai/y")

    save.assert_not_called()
    assert result is None
    out = capsys.readouterr().out.replace("\n", "")
    assert "not a table" in out
    assert "config.local.toml" in out
    assert local.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("shape", ["plain", "symlink"])
def test_onboard_recovery_restores_defaults_for_both_config_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """Recovering a corrupt config must not depend on how it was linked.

    A plain corrupt ``config.toml`` is removed and rewritten with the full
    ``default_config``. A symlinked one used to be replaced by an empty file,
    so its rewrite kept only ``[models]`` and silently dropped ``[user]`` and
    ``[prompt]``.
    """
    from gptme.cli import onboard

    config_dir = tmp_path / "gptme"
    config_dir.mkdir()
    main = config_dir / "config.toml"
    corrupt = '[models\ndefault = "broken"\n'
    if shape == "plain":
        main.write_text(corrupt, encoding="utf-8")
    else:
        target = tmp_path / "dotfiles" / "config.toml"
        target.parent.mkdir()
        target.write_text(corrupt, encoding="utf-8")
        main.symlink_to(target)

    monkeypatch.setattr(user_mod, "config_path", str(main))

    with (
        patch.object(onboard, "config_path", str(main)),
        patch.object(
            onboard,
            "get_config",
            side_effect=tomlkit.exceptions.TOMLKitError("corrupt"),
        ),
        patch.object(onboard.Confirm, "ask", return_value=True),
        patch("gptme.config.core.reload_config"),
    ):
        assert onboard._create_config("openai", "openai/new") is not None

    rewritten = tomlkit.loads(main.read_text(encoding="utf-8")).unwrap()
    assert rewritten["models"]["default"] == "openai/new"
    assert rewritten["user"]["name"] == "User"
    assert "gptme" in rewritten["prompt"]["project"]


@posix_only
def test_onboard_recovered_symlinked_local_config_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The placeholder that keeps a corrupt local config linked is owner-only."""
    from gptme.cli import onboard

    config_dir = tmp_path / "gptme"
    config_dir.mkdir()
    main = config_dir / "config.toml"
    main.write_text('[env]\nEXISTING = "keep"\n', encoding="utf-8")
    target = tmp_path / "dotfiles" / "secrets.toml"
    target.parent.mkdir()
    target.write_text('[models\ndefault = "broken"\n', encoding="utf-8")
    local = config_dir / "config.local.toml"
    local.symlink_to(target)

    monkeypatch.setattr(user_mod, "config_path", str(main))

    with (
        patch.object(onboard, "config_path", str(main)),
        patch.object(
            onboard,
            "get_config",
            side_effect=tomlkit.exceptions.TOMLKitError("corrupt"),
        ),
        patch.object(onboard.Confirm, "ask", return_value=True),
        patch("gptme.config.core.reload_config"),
    ):
        assert onboard._create_config("openai", "openai/new") is not None

    assert local.is_symlink()
    assert _mode(target) == 0o600
    # Recovering the local layer must not write built-in defaults into it.
    assert tomlkit.loads(target.read_text(encoding="utf-8")).unwrap() == {}


@pytest.mark.parametrize("descriptor", ["writable", "read_only"])
@pytest.mark.parametrize("err", ["ENOLCK", "EOPNOTSUPP", "ENOSYS"])
def test_lock_unsupported_by_filesystem_saves_unlocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    descriptor: str,
    err: str,
) -> None:
    """A filesystem that cannot lock at all must not fail every config save.

    Some FUSE and network mounts answer flock(2) with ENOLCK, EOPNOTSUPP or
    ENOSYS. That says nothing about another writer, so warn and save unlocked
    on either descriptor rather than raising.
    """
    if user_mod.fcntl is None:
        pytest.skip("posix flock only")
    code = getattr(errno, err)
    real_flock = user_mod.fcntl.flock

    def unsupported_flock(fd: int, operation: int) -> None:
        if operation == user_mod.fcntl.LOCK_EX:
            raise OSError(code, os.strerror(code))
        real_flock(fd, operation)

    if descriptor == "read_only":
        real_open = Path.open

        def lock_writable_only(self: Path, mode: str = "r", *args: Any, **kwargs: Any):
            if self.name.endswith(".lock") and mode == "a+b":
                raise PermissionError(errno.EACCES, "Permission denied", str(self))
            return real_open(self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", lock_writable_only)
        (tmp_path / ".config.toml.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(user_mod.fcntl, "flock", unsupported_flock)
    path = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(path))

    with caplog.at_level(logging.WARNING):
        set_config_value("user.name", "new", reload=False)

    assert "without it" in caplog.text
    assert 'name = "new"' in path.read_text(encoding="utf-8")


def test_other_lock_errors_on_writable_descriptor_still_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only "cannot lock here" errors degrade; a real I/O error still surfaces."""
    if user_mod.fcntl is None:
        pytest.skip("posix flock only")

    def broken_flock(fd: int, operation: int) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(user_mod.fcntl, "flock", broken_flock)
    path = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(path))

    with pytest.raises(OSError, match="Input/output error"):
        set_config_value("user.name", "new", reload=False)
    assert not path.exists()


def test_onboard_refuses_when_main_models_is_not_a_table(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A scalar `models` in config.toml used to escape as a ValueError."""
    from gptme.cli import onboard

    config_file.write_text('models = "oops"\n', encoding="utf-8")

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            return_value=SimpleNamespace(
                user=SimpleNamespace(models=SimpleNamespace(default=None), env={})
            ),
        ),
    ):
        result = onboard._create_config("openai", "openai/y")

    assert result is None
    out = capsys.readouterr().out.replace("\n", "")
    assert "not a table" in out
    assert "config.toml" in out
    assert config_file.read_text(encoding="utf-8") == 'models = "oops"\n'


def test_onboard_confirms_before_shadowing_user_env_model(
    config_file: Path,
) -> None:
    """[models].default outranks the user config's [env].MODEL.

    Saving a default without asking would silently replace the model the user
    already chose through that variable.
    """
    from gptme.cli import onboard

    config_file.write_text('[env]\nMODEL = "anthropic/old"\n', encoding="utf-8")

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            return_value=SimpleNamespace(
                user=SimpleNamespace(
                    models=SimpleNamespace(default=None),
                    env={"MODEL": "anthropic/old"},
                )
            ),
        ),
        patch.object(onboard.Confirm, "ask", return_value=False) as ask,
        patch.object(onboard, "set_config_value") as save,
    ):
        result = onboard._create_config("openai", "openai/new")

    assert result is None
    save.assert_not_called()
    assert "openai/new" in ask.call_args.args[0]


def test_onboard_recovery_reports_a_config_that_is_still_invalid(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After moving a corrupt file, a surviving invalid config must not crash.

    ``hooks = "x"`` parses as TOML but fails ``UserConfig`` with ValueError.
    The re-read used to catch only TOMLKitError, so onboarding aborted with a
    traceback after the corrupt file had already been moved.
    """
    from gptme.cli import onboard

    config_file.write_text('[models\ndefault = "broken"\n', encoding="utf-8")

    with (
        patch.object(onboard, "config_path", str(config_file)),
        patch.object(
            onboard,
            "get_config",
            side_effect=[
                tomlkit.exceptions.TOMLKitError("corrupt"),
                ValueError("hooks must be an object"),
            ],
        ),
        patch.object(onboard.Confirm, "ask", return_value=True),
        patch.object(onboard, "set_config_value") as save,
    ):
        result = onboard._create_config("openai", "openai/new")

    assert result is None
    save.assert_not_called()
    out = capsys.readouterr().out.replace("\n", "")
    assert "still invalid" in out
    assert "hooks must be an object" in out
    assert list(config_file.parent.glob("config.toml.corrupt-*"))


def test_scalar_prompt_section_warns_instead_of_crashing(
    config_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A top-level ``prompt = 1`` must not crash config loading with TypeError.

    The scalar reaches ``_filter_known_fields`` as an int, where ``set(data)``
    raised ``TypeError``. Like the other section-type mismatches, it now warns
    and falls back to defaults instead of aborting every config load.
    """
    config_file.write_text("prompt = 1\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="gptme.config.user"):
        config = load_user_config(str(config_file))

    assert config.prompt.about_user is None
    assert any("[prompt] should be a table" in r.message for r in caplog.records)


def test_onboard_recovery_handles_a_scalar_prompt_in_surviving_config(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real files: a corrupt main config plus a local ``prompt = 1``.

    Recovery moves the unparseable main file aside, then re-reads the surviving
    local config. That re-read raised ``TypeError`` from the scalar prompt and
    aborted onboarding with a traceback after the backup had already happened.
    It must now complete and save.
    """
    from gptme.cli import onboard

    local = config_file.with_name("config.local.toml")
    local.write_text("prompt = 1\n", encoding="utf-8")
    config_file.write_text('[models\ndefault = "broken"\n', encoding="utf-8")

    with (
        patch.object(onboard, "config_path", str(config_file)),
        # Real loader against the real files; the first read hits the corrupt
        # main config, the re-read sees the surviving local file.
        patch.object(
            onboard,
            "get_config",
            side_effect=lambda: SimpleNamespace(user=load_user_config()),
        ),
        patch.object(onboard.Confirm, "ask", return_value=True),
        patch("gptme.config.core.reload_config"),
    ):
        result = onboard._create_config("openai", "openai/new")

    assert result == config_file
    assert "Traceback" not in capsys.readouterr().out
    assert load_user_config(str(config_file)).models.default == "openai/new"
    assert list(config_file.parent.glob("config.toml.corrupt-*"))
