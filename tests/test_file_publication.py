"""Single-file writers must not publish a partially written replacement."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from gptme.message import Message


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX RLIMIT_FSIZE")
@pytest.mark.parametrize("operation", ["save", "patch"])
def test_real_file_size_failure_preserves_original(
    tmp_path: Path, operation: str
) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"ORIGINAL\n")
    code = r"""
import json, resource, signal, sys
from pathlib import Path
from gptme.tools.save import execute_save_impl
from gptme.tools.patch import execute_patch_impl
path = Path(sys.argv[1])
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (128, 128))
try:
    if sys.argv[2] == "save":
        list(execute_save_impl("X" * 8192, path))
    else:
        patch = "<<<<<<< ORIGINAL\nORIGINAL\n=======\n" + "X" * 8192 + "\n>>>>>>> UPDATED"
        list(execute_patch_impl(patch, path))
except OSError as err:
    print(json.dumps({"errno": err.errno}))
else:
    raise AssertionError("expected real write failure")
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(target), operation],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    import errno

    assert json.loads(result.stdout.strip().splitlines()[-1])["errno"] == errno.EFBIG
    assert target.read_bytes() == b"ORIGINAL\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target.txt"]


@pytest.mark.parametrize("operation", ["helper", "save", "patch"])
def test_windows_existing_destination_refused_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from types import SimpleNamespace

    from gptme.tools.patch import execute_patch_impl
    from gptme.tools.save import execute_save_impl
    from gptme.util import file_write

    target = tmp_path / "target.txt"
    target.write_bytes(b"PRIVATE\n")
    before = target.stat()
    opened = []

    def track_open(*args, **kwargs):
        opened.append(args[0])
        return os.open(*args, **kwargs)

    # Isolate the platform branch without changing pathlib's host OS selection.
    # The same test runs on native Windows in the path/config CI slice.
    monkeypatch.setattr(
        file_write,
        "os",
        SimpleNamespace(**(vars(os) | {"name": "nt", "open": track_open})),
    )
    with pytest.raises(ValueError, match="Windows security descriptor"):
        if operation == "helper":
            file_write.write_text_preserving_original(target, "PUBLIC\n", newline="")
        elif operation == "save":
            list(execute_save_impl("PUBLIC\n", target))
        else:
            list(
                execute_patch_impl(
                    "<<<<<<< ORIGINAL\nPRIVATE\n=======\nPUBLIC\n>>>>>>> UPDATED",
                    target,
                )
            )
    assert not opened
    assert target.read_bytes() == b"PRIVATE\n"
    assert target.stat().st_ino == before.st_ino
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target.txt"]


@pytest.mark.parametrize("operation", ["helper", "save"])
def test_windows_new_destination_still_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from types import SimpleNamespace

    from gptme.tools.save import execute_save_impl
    from gptme.util import file_write

    target = tmp_path / "new.txt"
    monkeypatch.setattr(
        file_write, "os", SimpleNamespace(**(vars(os) | {"name": "nt"}))
    )
    if operation == "helper":
        file_write.write_text_preserving_original(target, "NEW\n", newline="")
    else:
        list(execute_save_impl("NEW\n", target))
    assert target.read_bytes() == b"NEW\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.txt"]


@pytest.mark.parametrize("operation", ["helper", "save", "patch"])
def test_macos_existing_destination_refused_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from gptme.tools.patch import execute_patch_impl
    from gptme.tools.save import execute_save_impl
    from gptme.util import file_write

    target = tmp_path / "target.txt"
    target.write_bytes(b"PRIVATE\n")
    before = target.stat()
    opened = []
    real_open = os.open

    def track_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(file_write.os, "open", track_open)
    # macOS may expose no xattrs even when the file has a deny ACL.
    monkeypatch.setattr(file_write.os, "listxattr", lambda path: [], raising=False)
    with pytest.raises(ValueError, match="macOS ACL"):
        if operation == "helper":
            file_write.write_text_preserving_original(target, "PUBLIC\n", newline="")
        elif operation == "save":
            list(execute_save_impl("PUBLIC\n", target))
        else:
            list(
                execute_patch_impl(
                    "<<<<<<< ORIGINAL\nPRIVATE\n=======\nPUBLIC\n>>>>>>> UPDATED",
                    target,
                )
            )
    assert not opened
    assert target.read_bytes() == b"PRIVATE\n"
    assert target.stat().st_ino == before.st_ino
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target.txt"]


@pytest.mark.parametrize("operation", ["helper", "save"])
def test_macos_new_destination_still_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from gptme.tools.save import execute_save_impl
    from gptme.util.file_write import write_text_preserving_original

    target = tmp_path / "new.txt"
    monkeypatch.setattr(sys, "platform", "darwin")
    if operation == "helper":
        write_text_preserving_original(target, "NEW\n", newline="")
    else:
        list(execute_save_impl("NEW\n", target))
    assert target.read_bytes() == b"NEW\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.txt"]


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux xattr semantics"
)
@pytest.mark.parametrize("operation", ["save", "patch"])
@pytest.mark.parametrize("location", ["file", "parent"])
def test_unsupported_xattrs_allow_plain_file_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, location: str
) -> None:
    import errno

    from gptme.tools.patch import execute_patch_impl
    from gptme.tools.save import execute_save_impl
    from gptme.util import file_write

    target = tmp_path / "target.txt"
    target.write_bytes(b"ORIGINAL\n")
    target.chmod(0o640)

    def listxattr(path):
        if path == (target if location == "file" else tmp_path):
            raise OSError(errno.ENOTSUP, "Extended attributes not supported")
        return []

    monkeypatch.setattr(file_write.os, "listxattr", listxattr)
    if operation == "save":
        list(execute_save_impl("REPLACEMENT\n", target))
    else:
        list(
            execute_patch_impl(
                "<<<<<<< ORIGINAL\nORIGINAL\n=======\nREPLACEMENT\n>>>>>>> UPDATED",
                target,
            )
        )
    assert target.read_bytes() == b"REPLACEMENT\n"
    assert target.stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target.txt"]


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux xattr semantics"
)
@pytest.mark.parametrize("error", [13, 5])  # EACCES, EIO
@pytest.mark.parametrize("location", ["file", "parent"])
def test_xattr_inspection_errors_preserve_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: int, location: str
) -> None:
    from gptme.util import file_write

    target = tmp_path / "target.txt"
    target.write_bytes(b"PRIVATE\n")
    before = target.stat()

    def listxattr(path):
        if path == (target if location == "file" else tmp_path):
            raise OSError(error, "Cannot inspect metadata")
        return []

    monkeypatch.setattr(file_write.os, "listxattr", listxattr)
    with pytest.raises(OSError, match="Cannot inspect metadata") as exc:
        file_write.write_text_preserving_original(target, "PUBLIC\n", newline="")
    assert exc.value.errno == error
    assert target.read_bytes() == b"PRIVATE\n"
    assert target.stat().st_ino == before.st_ino
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target.txt"]


@pytest.mark.parametrize("phase", ["write", "close", "replace"])
@pytest.mark.parametrize("exists", [True, False])
def test_failure_cleans_staging_and_preserves_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, exists: bool
) -> None:
    from gptme.util import file_write

    target = tmp_path / "target.txt"
    if exists:
        target.write_bytes(b"ORIGINAL\n")
    real_fdopen = file_write.os.fdopen

    class FailingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, content):
            if phase == "write":
                self.stream.write(content[:2])
                raise OSError("injected write failure")
            return self.stream.write(content)

        def __exit__(self, *args):
            self.stream.close()
            if phase == "close":
                raise OSError("injected close failure")

    if phase == "replace":

        def fail_replace(*args):
            raise OSError("injected replace failure")

        monkeypatch.setattr(file_write.os, "replace", fail_replace)
    else:
        monkeypatch.setattr(
            file_write.os,
            "fdopen",
            lambda *args, **kwargs: FailingStream(real_fdopen(*args, **kwargs)),
        )
    with pytest.raises(OSError, match=f"injected {phase} failure"):
        file_write.write_text_preserving_original(target, "REPLACEMENT\n", newline="")
    assert target.read_bytes() == b"ORIGINAL\n" if exists else not target.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == (
        ["target.txt"] if exists else []
    )


@pytest.mark.parametrize("operation", ["save", "patch"])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_success_preserves_mode_symlink_and_endings(
    tmp_path: Path, operation: str, ending: str, requires_symlinks: None
) -> None:
    import stat

    from gptme.tools.patch import execute_patch_impl
    from gptme.tools.save import execute_save_impl

    target = tmp_path / "real.txt"
    target.write_bytes(("ORIGINAL" + ending).encode())
    target.chmod(0o754)
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    if operation == "save":
        messages = list(execute_save_impl("REPLACEMENT\n", link))
    else:
        messages = list(
            execute_patch_impl(
                "<<<<<<< ORIGINAL\nORIGINAL\n=======\nREPLACEMENT\n>>>>>>> UPDATED",
                link,
            )
        )
    assert messages
    assert target.read_bytes() == ("REPLACEMENT" + ending).encode()
    assert link.is_symlink()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o754
    assert sorted(p.name for p in tmp_path.iterdir()) == ["link.txt", "real.txt"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes and hard links")
def test_new_file_mode_matches_ordinary_creation(tmp_path: Path) -> None:
    import stat

    from gptme.util.file_write import write_text_preserving_original

    control = tmp_path / "control"
    control.write_text("control")
    target = tmp_path / "target"
    write_text_preserving_original(target, "replacement", newline="")
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(control.stat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard links")
@pytest.mark.parametrize("operation", ["save", "patch"])
def test_hard_links_fail_closed_without_splitting_aliases(
    tmp_path: Path, operation: str
) -> None:
    from gptme.tools.patch import execute_patch_impl
    from gptme.tools.save import execute_save_impl

    target = tmp_path / "target"
    alias = tmp_path / "alias"
    target.write_text("ORIGINAL\n")
    os.link(target, alias)
    with pytest.raises(ValueError, match="hard-linked"):
        if operation == "save":
            list(execute_save_impl("REPLACEMENT\n", target))
        else:
            list(
                execute_patch_impl(
                    "<<<<<<< ORIGINAL\nORIGINAL\n=======\nREPLACEMENT\n>>>>>>> UPDATED",
                    target,
                )
            )
    assert target.read_text() == alias.read_text() == "ORIGINAL\n"
    assert target.stat().st_ino == alias.stat().st_ino


@pytest.mark.parametrize("operation", ["save", "patch"])
def test_publication_failure_never_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    import gptme.hooks as hooks
    from gptme.hooks import HookType
    from gptme.tools import patch as patch_tool
    from gptme.tools import save
    from gptme.util import file_write

    target = tmp_path / "target"
    target.write_text("ORIGINAL\n")
    events = []

    def trigger(hook_type, *args, **kwargs):
        events.append(hook_type)
        return []

    def fail(*args):
        raise OSError("publication failed")

    monkeypatch.setattr(hooks, "trigger_hook", trigger)
    monkeypatch.setattr(file_write.os, "replace", fail)
    messages: list[Message] = []
    with pytest.raises(OSError, match="publication failed"):
        if operation == "save":
            messages.extend(save.execute_save_impl("REPLACEMENT\n", target))
        else:
            messages.extend(
                patch_tool.execute_patch_impl(
                    "<<<<<<< ORIGINAL\nORIGINAL\n=======\nREPLACEMENT\n>>>>>>> UPDATED",
                    target,
                )
            )
    assert not messages
    assert HookType.FILE_SAVE_POST not in events
    assert target.read_text() == "ORIGINAL\n"


@pytest.mark.parametrize("phase", ["create", "fdopen", "chmod"])
def test_staging_setup_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from gptme.util import file_write

    target = tmp_path / "target"
    target.write_text("ORIGINAL\n")
    real_open = file_write.os.open

    def fail(*args, **kwargs):
        raise OSError(f"injected {phase} failure")

    if phase == "create":

        def create(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                return fail()
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(file_write.os, "open", create)
    else:
        monkeypatch.setattr(file_write.os, phase, fail)
    with pytest.raises(OSError, match=f"injected {phase} failure"):
        file_write.write_text_preserving_original(target, "REPLACEMENT\n", newline="")
    assert target.read_text() == "ORIGINAL\n"
    assert [p.name for p in tmp_path.iterdir()] == ["target"]


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="requires POSIX non-root permissions",
)
def test_read_only_destination_not_bypassed(tmp_path: Path) -> None:
    from gptme.util.file_write import write_text_preserving_original

    target = tmp_path / "target"
    target.write_text("ORIGINAL\n")
    target.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            write_text_preserving_original(target, "REPLACEMENT\n", newline="")
        assert target.read_text() == "ORIGINAL\n"
        assert [p.name for p in tmp_path.iterdir()] == ["target"]
    finally:
        target.chmod(0o644)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_private_original_never_staged_with_broader_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    from gptme.util import file_write

    target = tmp_path / "target"
    target.write_text("PRIVATE\n")
    target.chmod(0o600)
    original_fdopen = file_write.os.fdopen
    modes = []

    def fdopen(fd, *args, **kwargs):
        modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(file_write.os, "fdopen", fdopen)
    file_write.write_text_preserving_original(target, "STILL PRIVATE\n", newline="")
    assert modes and all(mode & 0o077 == 0 for mode in modes)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="requires extended attributes")
def test_metadata_bearing_original_fails_closed(tmp_path: Path) -> None:
    from gptme.util.file_write import write_text_preserving_original

    target = tmp_path / "target"
    target.write_text("ORIGINAL\n")
    try:
        os.setxattr(target, "user.gptme-test", b"retain")
    except OSError:
        pytest.skip("filesystem does not support user extended attributes")
    with pytest.raises(ValueError, match="metadata"):
        write_text_preserving_original(target, "REPLACEMENT\n", newline="")
    assert target.read_text() == "ORIGINAL\n"
    assert os.getxattr(target, "user.gptme-test") == b"retain"
    assert [p.name for p in tmp_path.iterdir()] == ["target"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX NAME_MAX")
def test_long_valid_destination_name(tmp_path: Path) -> None:
    from gptme.util.file_write import write_text_preserving_original

    target = tmp_path / ("x" * 220)
    target.write_text("ORIGINAL\n")
    write_text_preserving_original(target, "REPLACEMENT\n", newline="")
    assert target.read_text() == "REPLACEMENT\n"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux POSIX ACL encoding"
)
@pytest.mark.parametrize("default_acl", [False, True])
def test_posix_acl_refused_without_changing_security(
    tmp_path: Path, default_acl: bool
) -> None:
    import struct

    from gptme.util.file_write import write_text_preserving_original

    target = tmp_path / "target"
    target.write_text("PRIVATE\n")
    entries = [
        (1, 6, 0xFFFFFFFF),
        (2, 0, 12345),
        (4, 4, 0xFFFFFFFF),
        (16, 4, 0xFFFFFFFF),
        (32, 4, 0xFFFFFFFF),
    ]
    acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", *entry) for entry in entries
    )
    location = tmp_path if default_acl else target
    attribute = "system.posix_acl_default" if default_acl else "system.posix_acl_access"
    try:
        os.setxattr(location, attribute, acl)
    except OSError:
        pytest.skip("filesystem does not support POSIX ACLs")
    before = os.getxattr(location, attribute)
    with pytest.raises(ValueError, match="metadata"):
        write_text_preserving_original(target, "REPLACEMENT\n", newline="")
    assert target.read_text() == "PRIVATE\n"
    assert os.getxattr(location, attribute) == before
    assert [p.name for p in tmp_path.iterdir()] == ["target"]
