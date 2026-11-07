"""Private, failure-safe publication of synthetic gptme.ai credentials."""

import json
import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.llm import llm_gptme as provider


@pytest.fixture
def token_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(provider, "_TOKEN_DIR", tmp_path / "auth")
    return provider._get_token_path()


def token(label: str = "synthetic") -> dict:
    return {"access_token": label, "expires_at": time.time() + 3600}


def test_save_load_and_service_keying(token_path: Path) -> None:
    value = token()
    provider._save_token(value)
    assert provider._load_token() == value
    other = "https://synthetic.invalid"
    provider._save_token(token("other"), other)
    other_token = provider._load_token(other)
    default_token = provider._load_token()
    assert other_token is not None and other_token["access_token"] == "other"
    assert default_token is not None and default_token["access_token"] == "synthetic"
    if os.name == "posix":
        assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission mode contract")
def test_contents_are_private_before_publication(token_path: Path) -> None:
    original_replace = os.replace
    observed = []

    def observe(source: str | Path, destination: str | Path) -> None:
        staged = Path(source)
        observed.append(stat.S_IMODE(staged.stat().st_mode))
        assert json.loads(staged.read_text())["access_token"] == "synthetic"
        assert not token_path.exists()
        return original_replace(source, destination)

    original_fdopen = os.fdopen

    def observe_open(fd, *args, **kwargs):
        # Assert privacy before the first write, not just after serialization.
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        return original_fdopen(fd, *args, **kwargs)

    with (
        patch.object(provider.os, "replace", observe),
        patch.object(provider.os, "fdopen", observe_open),
    ):
        provider._save_token(token())
    assert observed == [0o600]


@pytest.mark.parametrize("existing", [False, True])
def test_replace_failure_preserves_original_and_cleans_staging(
    token_path: Path, existing: bool
) -> None:
    if existing:
        provider._save_token(token("old"))
    before = token_path.read_bytes() if existing else None
    with (
        patch.object(
            provider.os, "replace", side_effect=OSError("synthetic replace failure")
        ),
        pytest.raises(OSError, match="synthetic replace failure"),
    ):
        provider._save_token(token("new"))
    assert (token_path.read_bytes() if token_path.exists() else None) == before
    assert list(token_path.parent.iterdir()) == ([token_path] if existing else [])


@pytest.mark.parametrize("stage", ["write", "close"])
def test_stream_failure_keeps_previous_login(token_path: Path, stage: str) -> None:
    provider._save_token(token("old"))
    before = token_path.read_bytes()
    original_fdopen = os.fdopen

    class InterruptedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, content):
            self.stream.write(content[:7] if stage == "write" else content)
            if stage == "write":
                raise OSError("synthetic write failure")

        def __exit__(self, *args):
            self.stream.close()
            if stage == "close":
                raise OSError("synthetic close failure")

    with (
        patch.object(
            provider.os,
            "fdopen",
            side_effect=lambda *a, **kw: InterruptedStream(original_fdopen(*a, **kw)),
        ),
        pytest.raises(OSError, match=f"synthetic {stage} failure"),
    ):
        provider._save_token(token("new"))
    assert token_path.read_bytes() == before
    previous = provider._load_token()
    assert previous is not None and previous["access_token"] == "old"
    assert list(token_path.parent.iterdir()) == [token_path]


def test_fdopen_failure_closes_descriptor_and_removes_staging(token_path: Path) -> None:
    provider._save_token(token("old"))
    before = token_path.read_bytes()
    descriptors: list[int] = []

    def fail_open(fd: int, *args, **kwargs):
        descriptors.append(fd)
        raise OSError("synthetic fdopen failure")

    with (
        patch.object(provider.os, "fdopen", fail_open),
        pytest.raises(OSError, match="synthetic fdopen failure"),
    ):
        provider._save_token(token("new"))
    assert len(descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptors[0])
    assert token_path.read_bytes() == before
    assert list(token_path.parent.iterdir()) == [token_path]


def test_serialization_failure_does_not_touch_previous_login(token_path: Path) -> None:
    provider._save_token(token("old"))
    before = token_path.read_bytes()
    with pytest.raises(TypeError):
        provider._save_token({"access_token": object()})
    assert token_path.read_bytes() == before
    assert list(token_path.parent.iterdir()) == [token_path]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_refuse_destination_aliases_and_non_files(token_path: Path, kind: str) -> None:
    token_path.parent.mkdir()
    sentinel = token_path.parent / "sentinel"
    sentinel.write_text("keep")
    if kind == "symlink":
        try:
            token_path.symlink_to(sentinel)
        except OSError:
            pytest.skip("symlinks unavailable")
    elif kind == "hardlink":
        os.link(sentinel, token_path)
    else:
        token_path.mkdir()
    with pytest.raises((OSError, ValueError)):
        provider._save_token(token())
    assert sentinel.read_text() == "keep"
    assert len(list(token_path.parent.iterdir())) == 2


@pytest.mark.skipif(sys.platform != "linux", reason="child-only Linux RLIMIT_FSIZE")
@pytest.mark.parametrize("limited", [False, True])
def test_real_file_size_limit_preserves_previous_login(
    tmp_path: Path, limited: bool
) -> None:
    script = textwrap.dedent("""
        import errno, json, signal, resource, sys, time
        from pathlib import Path
        from gptme.llm import llm_gptme as provider
        provider._TOKEN_DIR = Path(sys.argv[1]) / "auth"
        provider._save_token({"access_token": "synthetic-old", "expires_at": time.time()+3600})
        target = provider._get_token_path()
        before = target.read_bytes()
        limited = sys.argv[2] == "True"
        if limited:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (512, 512))
        error = None
        try:
            provider._save_token({"access_token": "X"*8192, "expires_at": time.time()+3600})
        except OSError as exc:
            error = exc.errno
        if limited:
            assert error == errno.EFBIG, error
            assert target.read_bytes() == before, "previous token was truncated"
        else:
            assert error is None
            assert len(target.read_bytes()) > 8192
        assert list(target.parent.iterdir()) == [target], "staging file leaked"
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(limited)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
