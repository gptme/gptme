"""Atomic conversation renames retain config ownership before staging contents."""

import errno
import os
import stat
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gptme.logmanager import conversations

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file ownership")


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    conversation = conv_dir / "conversation.jsonl"
    conversation.write_text("", encoding="utf-8")
    path = conv_dir / "config.toml"
    path.write_text('[chat]\nname = "old"\n[env]\nKEY = "secret"\n', encoding="utf-8")
    path.chmod(0o640)
    monkeypatch.setattr(
        conversations,
        "get_conversation_by_id",
        lambda _: SimpleNamespace(path=str(conversation)),
    )
    return path


def test_rename_retains_private_group(config: Path) -> None:
    """A replacement must not grant the process's primary group config access."""
    groups = set(os.getgroups()) - {config.stat().st_gid}
    if not groups:
        pytest.skip("requires membership in a second group")
    os.chown(config, -1, min(groups))
    before = config.stat()

    assert conversations.rename_conversation("conv", "new")

    after = config.stat()
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert stat.S_IMODE(after.st_mode) == 0o640
    assert 'name = "new"' in config.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux POSIX ACL xattrs")
@pytest.mark.parametrize("explicit_acl", [False, True])
def test_rename_preserves_access_acl(config: Path, explicit_acl: bool) -> None:
    """Restoring group mode must not activate inherited named-user access."""

    # Linux's version-2 POSIX ACL wire format: owner, named user, owning group,
    # mask, other. Use a numeric user distinct from the owner (no user required).
    def acl(named_user: int) -> bytes:
        return struct.pack("<I", 2) + b"".join(
            struct.pack("<HHI", tag, permissions, identifier)
            for tag, permissions, identifier in [
                (1, 6, 0xFFFFFFFF),
                (2, 4, named_user),
                (4, 4, 0xFFFFFFFF),
                (16, 4, 0xFFFFFFFF),
                (32, 0, 0xFFFFFFFF),
            ]
        )

    try:
        os.setxattr(config.parent, "system.posix_acl_default", acl(65534))
        if explicit_acl:
            os.setxattr(config, "system.posix_acl_access", acl(65533))
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            pytest.skip("filesystem does not support POSIX ACLs")
        raise

    assert conversations.rename_conversation("conv", "new")

    if explicit_acl:
        assert os.getxattr(config, "system.posix_acl_access") == acl(65533)
    else:
        with pytest.raises(OSError, match=os.strerror(errno.ENODATA)) as error:
            os.getxattr(config, "system.posix_acl_access")
        assert error.value.errno == errno.ENODATA
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert 'name = "new"' in config.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux POSIX ACL xattrs")
@pytest.mark.parametrize("operation", ["getxattr", "setxattr", "removexattr"])
def test_rename_acl_failure_leaves_original(
    config: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    original = config.read_bytes()
    inode = config.stat().st_ino

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("cannot preserve config ACL")

    if operation == "setxattr":
        monkeypatch.setattr(os, "getxattr", lambda *args: b"acl")
    monkeypatch.setattr(os, operation, refuse)

    with pytest.raises(PermissionError, match="cannot preserve config ACL"):
        conversations.rename_conversation("conv", "new")

    assert config.read_bytes() == original
    assert config.stat().st_ino == inode
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert not list(config.parent.glob(".config.*.toml.tmp"))


@pytest.mark.parametrize("refuse_ownership", [False, True])
def test_rename_establishes_ownership_before_writing(
    config: Path, monkeypatch: pytest.MonkeyPatch, refuse_ownership: bool
) -> None:
    original = config.read_bytes()
    original_stat = config.stat()
    real_fstat = os.fstat
    real_fchown = os.fchown
    seen_fds: list[int] = []

    def different_staged_group(fd: int) -> os.stat_result:
        info = list(real_fstat(fd))
        info[5] = original_stat.st_gid + 1
        return os.stat_result(info)

    def preserve_ownership(fd: int, uid: int, gid: int) -> None:
        # No private config content or broader mode may precede ownership setup.
        staged = real_fstat(fd)
        assert staged.st_size == 0
        assert stat.S_IMODE(staged.st_mode) == 0o600
        assert (uid, gid) == (original_stat.st_uid, original_stat.st_gid)
        assert config.read_bytes() == original
        seen_fds.append(fd)
        if refuse_ownership:
            raise PermissionError("cannot preserve config ownership")
        real_fchown(fd, uid, gid)

    dump = Mock(wraps=conversations.tomlkit.dump)
    monkeypatch.setattr(os, "fstat", different_staged_group)
    monkeypatch.setattr(os, "fchown", preserve_ownership)
    monkeypatch.setattr(conversations.tomlkit, "dump", dump)

    if refuse_ownership:
        with pytest.raises(PermissionError, match="cannot preserve config ownership"):
            conversations.rename_conversation("conv", "new")
        dump.assert_not_called()
        assert config.read_bytes() == original
        after = config.stat()
        assert (after.st_ino, after.st_uid, after.st_gid, after.st_mode) == (
            original_stat.st_ino,
            original_stat.st_uid,
            original_stat.st_gid,
            original_stat.st_mode,
        )
    else:
        assert conversations.rename_conversation("conv", "new")
        dump.assert_called_once()
        assert 'name = "new"' in config.read_text(encoding="utf-8")
        assert stat.S_IMODE(config.stat().st_mode) == 0o640

    assert len(seen_fds) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        real_fstat(seen_fds[0])
    assert not list(config.parent.glob(".config.*.toml.tmp"))
