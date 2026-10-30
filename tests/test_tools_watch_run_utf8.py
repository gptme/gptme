"""Run-watch text decoding must survive arbitrary pipe read boundaries."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gptme.tools import watch as watch_module


def _run_tail(payload: bytes, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Use a real pipe, but force byte-sized reads without timing assumptions."""
    with subprocess.Popen(
        [sys.executable, "-c", f"import os; os.write(1, {payload!r})"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        real_read = os.read

        def read_one_byte(read_fd: int, count: int) -> bytes:
            return real_read(read_fd, min(count, 1) if read_fd == fd else count)

        watch = watch_module.Watch(
            id="w-utf8",
            kind="run",
            description="UTF-8 pipe",
            created=time.time(),
            deadline=time.time() + 10,
            proc=proc,
            logdir=tmp_path,
        )
        try:
            with monkeypatch.context() as scoped:
                scoped.setattr(watch_module.os, "read", read_one_byte)
                watch_module._poll_run(watch, proc)
            assert proc.returncode == 0
            assert watch.fired
            assert len(watch.events) == 1
            return watch.events[0]
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


@pytest.mark.parametrize(
    "payload",
    [
        "€\n".encode(),
        "å漢🙂\n".encode(),
        b"plain ASCII\n",
        b"invalid:\xff\xfe\n",
        b"incomplete:\xe2\x82",
        b"x" * 1100 + "å漢🙂".encode(),
        b"x" * 1100 + b"\xe2\x82",
    ],
    ids=["euro", "mixed", "ascii", "invalid", "incomplete", "tail", "tail-eof"],
)
def test_run_decodes_split_utf8(
    payload: bytes, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = payload.decode("utf-8", errors="replace")[
        -watch_module._RUN_TAIL_CHARS :
    ]
    assert _run_tail(payload, monkeypatch, tmp_path) == (
        f"process exited rc=0 {expected}".strip()
    )


def test_run_decoder_state_is_not_shared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _run_tail(b"\xe2", monkeypatch, tmp_path) == "process exited rc=0 �"
    assert _run_tail(b"\x82\xac", monkeypatch, tmp_path) == "process exited rc=0 ��"
