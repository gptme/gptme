"""Private tmux fixture teardown must not strand its server on lookup errors."""

import importlib
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gptme.tools import tmux


@pytest.mark.skipif(os.name != "posix", reason="tmux uses POSIX filesystem paths")
@pytest.mark.parametrize(
    ("module_name", "fixture_name"),
    [
        ("test_tools_tmux_quoting", "private_tmux"),
        ("test_guardrail_tmux_cwd", "pane_tree"),
        ("test_tools_tmux_native_panes", "panes"),
    ],
)
@pytest.mark.parametrize("lookup_fails", [False, True])
def test_fixture_always_stops_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    fixture_name: str,
    lookup_fails: bool,
) -> None:
    module = importlib.import_module(module_name)
    # Surrogate-escaped bytes and trailing whitespace must survive path decoding.
    socket_path = tmp_path / os.fsdecode(b"socket-\xff ")
    unlinked: list[Path] = []
    monkeypatch.setattr(
        Path, "unlink", lambda path, *, missing_ok=False: unlinked.append(path)
    )
    killed = False

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        nonlocal killed
        if "display-message" in cmd:
            if lookup_fails:
                raise OSError("socket lookup failed")
            output = os.fsencode(socket_path) + b"\n"
            if kwargs.get("text"):
                output.decode("utf-8")  # Reproduce subprocess's strict decoding.
            return subprocess.CompletedProcess(cmd, 0, output, b"")
        if "kill-server" in cmd:
            killed = True
        return subprocess.CompletedProcess(
            cmd, 0, "%0\n" if kwargs.get("text") else b"", ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(tmux, "get_tmux_pane_cwd", lambda _: tmp_path / "pane")
    fixture = getattr(module, fixture_name).__wrapped__
    kwargs: dict[str, Any] = {"monkeypatch": monkeypatch}
    if fixture_name != "private_tmux":
        kwargs["tmp_path"] = tmp_path
    generator = fixture(**kwargs)
    next(generator)
    if lookup_fails:
        with pytest.raises(OSError, match="socket lookup failed"):
            next(generator)
    else:
        with pytest.raises(StopIteration):
            next(generator)
        assert unlinked == [socket_path]
    assert killed
