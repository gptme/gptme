"""Encoding backups must never grant readers access to private config bytes."""

import errno
import os
import stat
import struct
import sys
from pathlib import Path

import pytest

from gptme.config.user import _back_up_before_reencoding, _mark_encoding_unverified


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
@pytest.mark.parametrize("default_acl", [False, True])
def test_encoding_backup_stays_owner_only(tmp_path: Path, default_acl: bool) -> None:
    config = tmp_path / "config.toml"
    original = b'[env]\nKEY = "private"\n'
    config.write_bytes(original)
    config.chmod(0o640)
    groups = set(os.getgroups()) - {config.stat().st_gid}
    if groups:
        os.chown(config, -1, min(groups))

    if default_acl:
        if sys.platform != "linux":
            pytest.skip("Linux POSIX ACL xattrs")
        # The source predates this default grant; the backup will inherit it.
        acl = struct.pack("<I", 2) + b"".join(
            struct.pack("<HHI", tag, permissions, identifier)
            for tag, permissions, identifier in [
                (1, 6, 0xFFFFFFFF),
                (2, 4, 65534),
                (4, 4, 0xFFFFFFFF),
                (16, 4, 0xFFFFFFFF),
                (32, 0, 0xFFFFFFFF),
            ]
        )
        try:
            os.setxattr(tmp_path, "system.posix_acl_default", acl)
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                pytest.skip("filesystem does not support POSIX ACLs")
            raise

    _mark_encoding_unverified(config)
    _back_up_before_reencoding(config)

    backup = tmp_path / "config.toml.orig"
    assert backup.read_bytes() == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert config.read_bytes() == original
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert not list(tmp_path.glob("*.tmp"))
    if default_acl:
        # In an extended ACL, the mode's group bits are the effective mask.
        # Named users may remain recorded but must have no effective access.
        entries = os.getxattr(backup, "system.posix_acl_access")[4:]
        mask = [p for tag, p, _ in struct.iter_unpack("<HHI", entries) if tag == 16]
        assert mask == [0]
