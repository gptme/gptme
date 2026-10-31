"""Filesystem regressions for ChatConfig.save's failed-publication contract.

Only the selected filesystem operation is fault-injected. Config parsing, writes,
workspace entries and reloads otherwise use real files under pytest's tmp_path.
"""

import errno
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
import tomlkit

from gptme.config import ChatConfig


@dataclass
class PublicationState:
    logdir: Path
    old_workspace: Path
    new_workspace: Path
    old_bytes: bytes | None
    namespace: tuple[str, str | None]

    @property
    def entry(self) -> Path:
        return self.logdir / "workspace"

    @property
    def config_path(self) -> Path:
        return self.logdir / "config.toml"

    def candidate(self) -> ChatConfig:
        return ChatConfig(
            _logdir=self.logdir, model="test/new-model", workspace=self.new_workspace
        )


def _namespace(path: Path) -> tuple[str, str | None]:
    """Do not follow links: a dangling link is not an absent entry."""
    if path.is_symlink():
        return "symlink", os.readlink(path)
    if path.is_dir():
        return "directory", None
    assert not path.exists(), f"Unexpected workspace entry: {path}"
    return "absent", None


def _seed(tmp_path: Path, kind: str, saved: bool = True) -> PublicationState:
    logdir = tmp_path / "conversation"
    logdir.mkdir()
    entry = logdir / "workspace"
    old_workspace = tmp_path / "old-workspace"
    if kind == "empty":
        entry.mkdir()
        old_workspace = entry
    elif kind in ("symlink", "dangling"):
        # Relative targets also prove rollback preserves the namespace exactly,
        # rather than silently converting an old link into an absolute one.
        if kind == "symlink":
            old_workspace.mkdir()
            (old_workspace / "valuable.bin").write_bytes(b"old workspace\x00\xff")
        entry.symlink_to(Path("..") / old_workspace.name, target_is_directory=True)
    else:
        assert kind == "absent"
        old_workspace.mkdir()
        (old_workspace / "valuable.bin").write_bytes(b"old workspace\x00\xff")

    new_workspace = tmp_path / "new-workspace"
    new_workspace.mkdir()
    (new_workspace / "valuable.bin").write_bytes(b"new workspace\x00\xfe")
    old_bytes = None
    if saved:
        # Hand-written formatting/comments make exact-byte preservation stronger
        # than merely checking that a failed save left parseable TOML behind.
        text = "# Keep these exact bytes on failure.\n" + tomlkit.dumps(
            {"chat": {"model": "test/old-model", "workspace": str(old_workspace)}}
        )
        old_bytes = text.replace("\n", "\r\n").encode("utf-8")
        (logdir / "config.toml").write_bytes(old_bytes)
    return PublicationState(
        logdir, old_workspace, new_workspace, old_bytes, _namespace(entry)
    )


def _assert_preserved(state: PublicationState) -> None:
    assert _namespace(state.entry) == state.namespace
    if state.old_bytes is None:
        assert not state.config_path.exists()
    else:
        assert state.config_path.read_bytes() == state.old_bytes
        loaded = ChatConfig.from_logdir(state.logdir)
        assert loaded.model == "test/old-model"
        assert loaded.workspace == state.old_workspace
    if state.old_workspace != state.entry and state.old_workspace.exists():
        assert (
            state.old_workspace / "valuable.bin"
        ).read_bytes() == b"old workspace\x00\xff"
    if state.namespace[0] == "directory":
        assert list(state.entry.iterdir()) == []
    assert (
        state.new_workspace / "valuable.bin"
    ).read_bytes() == b"new workspace\x00\xfe"


def test_rejected_nonempty_workspace_preserves_persisted_state(tmp_path: Path) -> None:
    state = _seed(tmp_path, "empty")
    valuable = state.entry / "nested" / "valuable.bin"
    valuable.parent.mkdir()
    valuable.write_bytes(b"irreplaceable\x00\xff")

    with pytest.raises(ValueError, match="(?i)workspace"):
        state.candidate().save()

    assert state.config_path.read_bytes() == state.old_bytes
    loaded = ChatConfig.from_logdir(state.logdir)
    assert loaded.model == "test/old-model"
    assert loaded.workspace == state.entry
    assert not state.entry.is_symlink()
    assert valuable.read_bytes() == b"irreplaceable\x00\xff"
    assert sorted(p.relative_to(state.entry) for p in state.entry.rglob("*")) == [
        Path("nested"),
        Path("nested/valuable.bin"),
    ]
    assert (
        state.new_workspace / "valuable.bin"
    ).read_bytes() == b"new workspace\x00\xfe"


@pytest.mark.parametrize("kind", ["absent", "empty", "symlink", "dangling"])
@pytest.mark.parametrize("saved", [True, False], ids=["existing-config", "no-config"])
def test_symlink_publication_failure_preserves_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, saved: bool
) -> None:
    state = _seed(tmp_path, kind, saved)
    original = Path.symlink_to
    attempts = []

    def fail_new_link(
        path: Path, target: str | Path, target_is_directory: bool = False
    ) -> None:
        if path.parent == state.logdir and Path(target) == state.new_workspace:
            attempts.append(path)
            raise OSError(errno.EIO, "injected workspace publication failure")
        original(path, target, target_is_directory=target_is_directory)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "symlink_to", fail_new_link)
        with pytest.raises(OSError, match="injected workspace publication failure"):
            state.candidate().save()

    assert attempts, "The targeted workspace publication must actually be attempted"
    _assert_preserved(state)


@pytest.mark.parametrize("kind", ["absent", "empty", "symlink", "dangling"])
@pytest.mark.parametrize("saved", [True, False], ids=["existing-config", "no-config"])
def test_config_replace_failure_preserves_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, saved: bool
) -> None:
    state = _seed(tmp_path, kind, saved)
    original = Path.replace
    attempts = []

    def fail_config_replace(path: Path, target: str | Path) -> Path:
        if Path(target) == state.config_path:
            attempts.append(path)
            raise OSError(errno.EIO, "injected config replacement failure")
        return original(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_config_replace)
        with pytest.raises(OSError, match="injected config replacement failure"):
            state.candidate().save()

    assert attempts, "The targeted config publication must actually be attempted"
    _assert_preserved(state)


@pytest.mark.parametrize("kind", ["absent", "empty", "symlink", "dangling"])
def test_successful_workspace_transition(tmp_path: Path, kind: str) -> None:
    state = _seed(tmp_path, kind)
    candidate = state.candidate()
    assert candidate.save() is candidate
    assert state.entry.is_symlink()
    assert state.entry.resolve() == state.new_workspace
    loaded = ChatConfig.from_logdir(state.logdir)
    assert loaded.model == "test/new-model"
    assert loaded.workspace == state.new_workspace
    assert (
        state.new_workspace / "valuable.bin"
    ).read_bytes() == b"new workspace\x00\xfe"
    if kind == "symlink":
        assert (
            state.old_workspace / "valuable.bin"
        ).read_bytes() == b"old workspace\x00\xff"
    elif kind == "dangling":
        assert not state.old_workspace.exists()


def test_same_real_workspace_with_data_can_save(tmp_path: Path) -> None:
    state = _seed(tmp_path, "empty")
    valuable = state.entry / "valuable.bin"
    valuable.write_bytes(b"keep me")
    candidate = replace(state.candidate(), workspace=state.entry)
    assert candidate.save() is candidate
    assert not state.entry.is_symlink()
    assert valuable.read_bytes() == b"keep me"
    loaded = ChatConfig.from_logdir(state.logdir)
    assert loaded.model == "test/new-model"
    assert loaded.workspace == state.entry


def test_startup_default_workspace_can_save(tmp_path: Path) -> None:
    logdir = tmp_path / "new-conversation"
    config = ChatConfig.from_logdir(logdir)
    config.model = "test/startup-model"
    assert config.workspace == logdir / "workspace"
    assert config.save() is config
    assert config.workspace.is_dir()
    assert not config.workspace.is_symlink()
    loaded = ChatConfig.from_logdir(logdir)
    assert loaded.model == "test/startup-model"
    assert loaded.workspace == config.workspace


def test_windows_privilege_fallback_still_publishes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate WinError 1314; this is not a native Windows filesystem test."""
    state = _seed(tmp_path, "absent")
    original = Path.symlink_to
    attempts = []

    def privilege_failure(
        path: Path, target: str | Path, target_is_directory: bool = False
    ) -> None:
        if path.parent == state.logdir and Path(target) == state.new_workspace:
            attempts.append(path)
            error = OSError(errno.EPERM, "simulated Windows symlink privilege failure")
            error.winerror = 1314  # type: ignore[attr-defined]
            raise error
        original(path, target, target_is_directory=target_is_directory)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "symlink_to", privilege_failure)
        assert state.candidate().save().workspace == state.new_workspace

    assert attempts
    loaded = ChatConfig.from_logdir(state.logdir)
    assert loaded.model == "test/new-model"
    assert loaded.workspace == state.new_workspace
    assert (
        state.new_workspace / "valuable.bin"
    ).read_bytes() == b"new workspace\x00\xfe"


def test_rollback_failure_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed restore must not masquerade as an ordinary, preserved save failure."""
    state = _seed(tmp_path, "symlink")
    original_replace = Path.replace
    original_rename = Path.rename
    original_symlink = Path.symlink_to
    restores = []

    def is_old_link(path: Path) -> bool:
        return path.is_symlink() and os.readlink(path) == state.namespace[1]

    def fail_replace(path: Path, target: str | Path) -> Path:
        if Path(target) == state.config_path:
            raise OSError(errno.EIO, "injected config replacement failure")
        if Path(target) == state.entry and is_old_link(path):
            restores.append(path)
            raise OSError(errno.EIO, "injected workspace restoration failure")
        return original_replace(path, target)

    def fail_rename(path: Path, target: str | Path) -> Path:
        if Path(target) == state.entry and is_old_link(path):
            restores.append(path)
            raise OSError(errno.EIO, "injected workspace restoration failure")
        return original_rename(path, target)

    def fail_restore_link(
        path: Path, target: str | Path, target_is_directory: bool = False
    ) -> None:
        resolved_target = (path.parent / target).resolve()
        if path.parent == state.logdir and resolved_target == state.old_workspace:
            restores.append(path)
            raise OSError(errno.EIO, "injected workspace restoration failure")
        original_symlink(path, target, target_is_directory=target_is_directory)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_replace)
        patch.setattr(Path, "rename", fail_rename)
        patch.setattr(Path, "symlink_to", fail_restore_link)
        with pytest.raises((RuntimeError, OSError)) as caught:
            state.candidate().save()

    assert restores, "Exercise a real failed restoration after config publication fails"
    error = caught.value
    assert isinstance(error, RuntimeError) or error.__cause__ is not None
    assert re.search(r"(?i)roll.?back|restor", str(error)), str(error)
    # Rollback is explicitly not claimed here. The config must still be old and
    # neither external workspace's valuable data may be lost.
    assert state.config_path.read_bytes() == state.old_bytes
    assert (
        state.old_workspace / "valuable.bin"
    ).read_bytes() == b"old workspace\x00\xff"
    assert (
        state.new_workspace / "valuable.bin"
    ).read_bytes() == b"new workspace\x00\xfe"


@pytest.mark.parametrize("populated", [False, True], ids=["empty", "nonempty"])
@pytest.mark.parametrize("alias_kind", ["symlink-logdir", "relative-logdir"])
def test_local_workspace_through_logdir_alias_can_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    populated: bool,
    alias_kind: str,
) -> None:
    real_logdir = tmp_path / "conversation"
    real_logdir.mkdir()
    if alias_kind == "symlink-logdir":
        alias = tmp_path / "alias"
        alias.symlink_to(real_logdir, target_is_directory=True)
    else:
        monkeypatch.chdir(tmp_path)
        alias = Path("conversation")
    config = ChatConfig.from_logdir(alias)
    entry = real_logdir / "workspace"
    assert config.workspace == entry
    assert not entry.is_symlink()
    if populated:
        (entry / "valuable.bin").write_bytes(b"local data\x00\xff")
    before = entry.stat()
    config.model = "test/alias-model"

    assert config.save() is config

    assert not entry.is_symlink()
    assert entry.is_dir()
    assert entry.stat().st_ino == before.st_ino
    assert sorted(p.name for p in entry.iterdir()) == (
        ["valuable.bin"] if populated else []
    )
    if populated:
        assert (entry / "valuable.bin").read_bytes() == b"local data\x00\xff"
    loaded = ChatConfig.from_logdir(alias)
    assert loaded.model == "test/alias-model"
    assert loaded.workspace == entry


def test_displaced_backup_cleanup_failure_is_success_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    state = _seed(tmp_path, "symlink")
    original = Path.unlink
    retained = []

    def fail_displaced_link_cleanup(path: Path, missing_ok: bool = False) -> None:
        if (
            path != state.entry
            and state.logdir in path.parents
            and path.is_symlink()
            and os.readlink(path) == state.namespace[1]
        ):
            # The failure must be after commit, not a fault in workspace staging.
            assert ChatConfig.from_logdir(state.logdir).model == "test/new-model"
            retained.append(path)
            raise OSError(errno.EIO, "injected displaced backup cleanup failure")
        original(path, missing_ok=missing_ok)

    candidate = state.candidate()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_displaced_link_cleanup)
        with caplog.at_level("WARNING", logger="gptme.config.chat"):
            assert candidate.save() is candidate

    assert retained, "Exercise cleanup of a displaced old entry after commit"
    for path in retained:
        assert path.is_symlink()
        assert os.readlink(path) == state.namespace[1]
        assert str(path) in caplog.text
    assert re.search(r"(?i)retain|cleanup|remov", caplog.text)
    loaded = ChatConfig.from_logdir(state.logdir)
    assert loaded.model == "test/new-model"
    assert loaded.workspace == state.new_workspace
    assert state.entry.resolve() == state.new_workspace
    assert (
        state.old_workspace / "valuable.bin"
    ).read_bytes() == b"old workspace\x00\xff"
    assert (
        state.new_workspace / "valuable.bin"
    ).read_bytes() == b"new workspace\x00\xfe"


@pytest.mark.parametrize("kind", ["absent", "empty", "symlink", "dangling"])
@pytest.mark.parametrize("saved", [True, False], ids=["existing-config", "no-config"])
def test_prepublication_fsync_failure_leaves_no_staged_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, saved: bool
) -> None:
    state = _seed(tmp_path, kind, saved)
    original = os.fsync
    attempts = []

    def fail_staged_config_fsync(fd: int) -> None:
        opened = os.fstat(fd)
        for path in state.logdir.glob("*.toml.tmp"):
            staged = path.stat()
            if (opened.st_dev, opened.st_ino) == (staged.st_dev, staged.st_ino):
                attempts.append(path)
                raise OSError(errno.EIO, "injected staged config fsync failure")
        original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_staged_config_fsync)
        with pytest.raises(OSError, match="injected staged config fsync failure"):
            state.candidate().save()

    assert attempts, "Exercise fsync of the real staged config, not another file"
    _assert_preserved(state)
    assert list(state.logdir.glob("*.toml.tmp")) == []
