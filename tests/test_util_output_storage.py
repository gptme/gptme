"""Tests for the output_storage utility module."""

import errno
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gptme.util import output_storage
from gptme.util.output_storage import (
    UnsafeOutputPathError,
    create_tool_result_summary,
    large_output_path,
    open_private_output_file,
    save_large_output,
)


def test_save_large_output_creates_file(tmp_path: Path):
    """save_large_output should create a file with the content."""
    content = "Hello, world!\nLine 2\nLine 3"
    summary, saved_path = save_large_output(
        content=content,
        logdir=tmp_path,
        output_type="shell",
    )
    assert saved_path.exists()
    assert saved_path.read_text() == content
    assert "shell" in str(saved_path)
    assert "[Large shell output saved]" in summary


def test_save_large_output_with_metadata(tmp_path: Path):
    """save_large_output should include command and token info in summary."""
    summary, _ = save_large_output(
        content="output data",
        logdir=tmp_path,
        output_type="python",
        command_info="print('hello')",
        original_tokens=5000,
    )
    assert "Command: print('hello')" in summary
    assert "Original: 5000 tokens" in summary
    assert "Full output:" in summary


def test_save_large_output_unique_filenames(tmp_path: Path):
    """Different content should produce different filenames."""
    _, path1 = save_large_output(content="content A", logdir=tmp_path)
    _, path2 = save_large_output(content="content B", logdir=tmp_path)
    assert path1 != path2


def test_save_large_output_directory_structure(tmp_path: Path):
    """Output should be saved under tool-outputs/<type>/."""
    _, saved_path = save_large_output(
        content="test",
        logdir=tmp_path,
        output_type="shell",
    )
    assert "tool-outputs" in str(saved_path)
    assert "shell" in str(saved_path)


def test_create_tool_result_summary_no_logdir():
    """Without logdir, should return a simple message."""
    result = create_tool_result_summary(
        content="lots of output",
        original_tokens=10000,
        logdir=None,
        tool_name="shell",
    )
    assert "10000 tokens" in result
    assert "automatically removed" in result
    assert "saved to" not in result.lower()


def test_create_tool_result_summary_with_logdir(tmp_path: Path):
    """With logdir, should save output and reference the file."""
    result = create_tool_result_summary(
        content="lots of output",
        original_tokens=10000,
        logdir=tmp_path,
        tool_name="shell",
    )
    assert "10000 tokens" in result
    assert "saved to" in result.lower()
    assert "read or grep" in result


def test_create_tool_result_summary_extracts_command(tmp_path: Path):
    """Should extract command info from content."""
    content = "Ran command: ls -la\nfile1.txt\nfile2.txt"
    result = create_tool_result_summary(
        content=content,
        original_tokens=500,
        logdir=tmp_path,
        tool_name="shell",
    )
    assert "Ran command: ls -la" in result


def test_create_tool_result_summary_no_command(tmp_path: Path):
    """Content without command prefix should work fine."""
    content = "just some output\nwithout command info"
    result = create_tool_result_summary(
        content=content,
        original_tokens=500,
        logdir=tmp_path,
        tool_name="python",
    )
    assert "500 tokens" in result
    # Should not have command info
    assert "Command:" not in result or "Ran command:" not in result


def test_create_tool_result_summary_records_savings_ledger(tmp_path: Path):
    """create_tool_result_summary should append to context-savings.jsonl."""
    create_tool_result_summary(
        content="x" * 4000,  # ensure non-trivial original token count
        original_tokens=10000,
        logdir=tmp_path,
        tool_name="autocompact",
    )
    ledger = tmp_path / "context-savings.jsonl"
    assert ledger.exists(), "ledger should be created when logdir is provided"

    rows = [
        json.loads(line) for line in ledger.read_text().splitlines() if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "autocompact"
    assert row["original_tokens"] == 10000
    assert row["saved_tokens"] > 0
    assert row["saved_tokens"] == row["original_tokens"] - row["kept_tokens"]
    assert "saved_path" in row


def test_create_tool_result_summary_no_logdir_no_ledger(tmp_path: Path):
    """Without logdir, no ledger should be written anywhere."""
    create_tool_result_summary(
        content="lots of output",
        original_tokens=10000,
        logdir=None,
        tool_name="shell",
    )
    # Sanity: the test's tmp_path stays clean
    assert not (tmp_path / "context-savings.jsonl").exists()


# --- Writer safety (docs/design/file-backed-output.md, slice 1) ---

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")


@pytest.fixture(params=["dir_fd", "by_path"])
def writer_mode(request, monkeypatch):
    """Exercise both the descriptor-relative and path-based writers."""
    if request.param == "dir_fd" and not output_storage._USE_DIR_FD:
        pytest.skip("dir_fd traversal unsupported on this platform")
    monkeypatch.setattr(output_storage, "_USE_DIR_FD", request.param == "dir_fd")
    return request.param


def _freeze_time(monkeypatch):
    frozen = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr("gptme.util.output_storage.datetime", FrozenDateTime)


def test_same_second_same_content_keeps_distinct_files(
    tmp_path: Path, monkeypatch, writer_mode
):
    """Distinct calls must not share or clobber a file on a name collision."""
    _freeze_time(monkeypatch)
    _, first = save_large_output(content="same", logdir=tmp_path)
    _, second = save_large_output(content="same", logdir=tmp_path)
    _, third = save_large_output(content="same", logdir=tmp_path)

    assert first == large_output_path(tmp_path, "same")
    assert len({first, second, third}) == 3
    assert second.name == f"{first.stem}-1.txt"
    assert all(p.read_text() == "same" for p in (first, second, third))


def test_existing_restrictive_file_is_not_overwritten(
    tmp_path: Path, monkeypatch, writer_mode
):
    _freeze_time(monkeypatch)
    existing = large_output_path(tmp_path, "payload", "shell")
    existing.parent.mkdir(parents=True)
    existing.write_text("earlier evidence")
    existing.chmod(0o400)

    _, saved = save_large_output(
        content="payload", logdir=tmp_path, output_type="shell"
    )

    assert saved != existing
    assert existing.read_text() == "earlier evidence"
    assert saved.read_text() == "payload"


@pytest.mark.parametrize(
    ("output_type", "expected"),
    [
        ("../escape", "_escape"),
        ("a/b", "a_b"),
        ("..", "tool"),
        ("", "tool"),
        (".hidden", "hidden"),
        ("mcp:server/tool", "mcp_server_tool"),
        ("x" * 200, "x" * 64),
    ],
)
def test_unsafe_output_type_stays_inside_namespace(
    tmp_path: Path, output_type: str, expected: str
):
    _, saved = save_large_output(content="x", logdir=tmp_path, output_type=output_type)
    root = (tmp_path / "tool-outputs").resolve()
    assert saved.resolve().parent == root / expected
    assert large_output_path(tmp_path, "x", output_type).parent.name == expected


@posix_only
def test_private_modes_under_permissive_umask(tmp_path: Path, writer_mode):
    old = os.umask(0)
    try:
        _, saved = save_large_output(content="secret-ish", logdir=tmp_path)
    finally:
        os.umask(old)
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "tool-outputs").stat().st_mode) == 0o700


@posix_only
@pytest.mark.parametrize("component", ["tool-outputs", "tool-outputs/shell"])
def test_symlinked_parent_is_refused(tmp_path: Path, component: str, writer_mode):
    logdir = tmp_path / "conv"
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = logdir / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(UnsafeOutputPathError):
        save_large_output(content="data", logdir=logdir, output_type="shell")
    assert list(target.iterdir()) == []


@posix_only
def test_symlinked_destination_is_not_followed(
    tmp_path: Path, monkeypatch, writer_mode
):
    _freeze_time(monkeypatch)
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch")
    dest = large_output_path(tmp_path / "conv", "data", "shell")
    dest.parent.mkdir(parents=True)
    dest.symlink_to(victim)

    _, saved = save_large_output(
        content="data", logdir=tmp_path / "conv", output_type="shell"
    )

    assert victim.read_text() == "do not touch"
    assert saved != dest and not saved.is_symlink()
    assert saved.read_text() == "data"


def test_non_directory_component_is_refused(tmp_path: Path, writer_mode):
    (tmp_path / "tool-outputs").write_text("not a dir")
    with pytest.raises(UnsafeOutputPathError):
        save_large_output(content="data", logdir=tmp_path)


def test_write_failure_leaves_no_partial_or_temp_file(
    tmp_path: Path, monkeypatch, writer_mode
):
    def enospc(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(output_storage.os, "fsync", enospc)
    with pytest.raises(OSError, match="No space left"):
        save_large_output(content="data", logdir=tmp_path, output_type="shell")
    assert list((tmp_path / "tool-outputs" / "shell").iterdir()) == []


@pytest.mark.parametrize("err", [errno.EPERM, errno.EXDEV, errno.ENOTSUP])
def test_filesystem_without_hard_links_falls_back(
    tmp_path: Path, monkeypatch, writer_mode, err: int
):
    """os.link existing does not mean the filesystem supports hard links."""
    _freeze_time(monkeypatch)

    def no_links(*args, **kwargs):
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(output_storage.os, "link", no_links)
    _, first = save_large_output(content="data", logdir=tmp_path, output_type="shell")
    _, second = save_large_output(content="data", logdir=tmp_path, output_type="shell")

    assert first == large_output_path(tmp_path, "data", "shell")
    assert second.name == f"{first.stem}-1.txt"
    assert first.read_text() == second.read_text() == "data"
    assert sorted(p.name for p in first.parent.iterdir()) == sorted(
        [first.name, second.name]
    )
    if sys.platform != "win32":
        assert stat.S_IMODE(first.stat().st_mode) == 0o600


def test_fallback_write_failure_leaves_nothing(
    tmp_path: Path, monkeypatch, writer_mode
):
    def no_links(*args, **kwargs):
        raise OSError(errno.EPERM, "no hard links")

    calls = {"n": 0}
    real_fsync = os.fsync

    def fail_second_fsync(fd):
        calls["n"] += 1
        if calls["n"] == 2:  # first fsync is the temp file, second the fallback
            raise OSError(errno.ENOSPC, "No space left on device")
        real_fsync(fd)

    monkeypatch.setattr(output_storage.os, "link", no_links)
    monkeypatch.setattr(output_storage.os, "fsync", fail_second_fsync)
    with pytest.raises(OSError, match="No space left"):
        save_large_output(content="data", logdir=tmp_path, output_type="shell")
    assert list((tmp_path / "tool-outputs" / "shell").iterdir()) == []


def test_content_is_utf8_regardless_of_locale(tmp_path: Path, writer_mode):
    content = "naïve ✓ 日本 \udcff"  # includes a surrogateescape'd byte
    _, saved = save_large_output(content=content, logdir=tmp_path)
    assert saved.read_bytes() == "naïve ✓ 日本 ".encode() + b"\xff"


def test_lone_surrogate_is_saved_with_replacement(tmp_path: Path):
    _, saved = save_large_output(content="a\ud800b", logdir=tmp_path)
    assert saved.read_bytes() == b"a?b"


def test_unrelated_lone_surrogate_keeps_escaped_bytes(tmp_path: Path):
    """Replacement applies per character, not to the whole output."""
    _, saved = save_large_output(content="a\udcff\ud800b\udc80", logdir=tmp_path)
    assert saved.read_bytes() == b"a\xff?b\x80"


def test_concurrent_same_content_saves_are_distinct(tmp_path: Path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    _freeze_time(monkeypatch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(
            pool.map(
                lambda _: save_large_output(content="dup", logdir=tmp_path)[1],
                range(16),
            )
        )
    assert len(set(paths)) == 16
    assert all(p.read_text() == "dup" for p in paths)
    leftovers = [p for p in paths[0].parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_open_private_output_file_appends_and_keeps_distinct_collisions(
    tmp_path: Path, writer_mode
):
    first = open_private_output_file(tmp_path, "shell-jobs", "job-1-1.stdout.live")
    second = open_private_output_file(tmp_path, "shell-jobs", "job-1-1.stdout.live")
    first.append(b"BEGIN\n")
    first.append(b"END\n")
    first.close()
    second.append(b"other\n")
    second.close()

    assert first.path != second.path
    assert first.path.read_bytes() == b"BEGIN\nEND\n"
    assert second.path.read_bytes() == b"other\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(first.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(first.path.parent.stat().st_mode) == 0o700


def test_open_private_output_file_replace_does_not_follow_symlink(
    tmp_path: Path, writer_mode
):
    stream = open_private_output_file(tmp_path, "shell-jobs", "job.log")
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch")
    stream.path.unlink()
    stream.path.symlink_to(victim)
    stream.replace(b"owned inode\n")
    stream.close()
    assert victim.read_text() == "do not touch"


@posix_only
@pytest.mark.parametrize("component", ["tool-outputs", "tool-outputs/shell-jobs"])
def test_open_private_output_file_refuses_symlinked_parent(
    tmp_path: Path, component: str, writer_mode
):
    logdir = tmp_path / "conv"
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = logdir / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(UnsafeOutputPathError):
        open_private_output_file(logdir, "shell-jobs", "job.log")
    assert list(target.iterdir()) == []
