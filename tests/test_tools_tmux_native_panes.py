"""Native pane targeting must retain the gptme session namespace."""

import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gptme.tools import tmux


@pytest.fixture
def panes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, str]]:
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed")
    # Developer configuration must not change the fixture's zero-based targets.
    (tmp_path / ".tmux.conf").write_text(
        "set -g base-index 1\nset -g pane-base-index 1\n"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    socket = f"gptme-panes-{uuid.uuid4().hex}"
    run = subprocess.run

    def isolated_run(
        cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess:
        if cmd[0] == "tmux":
            cmd = ["tmux", "-L", socket, "-f", "/dev/null", *cmd[1:]]
        return run(cmd, *args, **kwargs)

    monkeypatch.setattr(tmux.subprocess, "run", isolated_run)
    monkeypatch.setattr(tmux, "sleep", lambda _: None)

    def query(args: list[str]) -> str:
        return isolated_run(
            ["tmux", *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        first = query(
            [
                "new-session",
                "-d",
                "-s",
                "gptme_1",
                "-P",
                "-F",
                "#{pane_id}",
                "stty -echo; cat",
            ]
        )
        second = query(
            [
                "split-window",
                "-h",
                "-t",
                "gptme_1",
                "-P",
                "-F",
                "#{pane_id}",
                "stty -echo; cat",
            ]
        )
        outside = query(
            [
                "new-session",
                "-d",
                "-s",
                "unrelated",
                "-P",
                "-F",
                "#{pane_id}",
                "stty -echo; cat",
            ]
        )
        yield {"first": first, "second": second, "outside": outside}
    finally:
        isolated_run(["tmux", "kill-server"], capture_output=True)


def wait_for(pane: str, marker: str) -> str:
    deadline = time.monotonic() + 3
    output = ""
    while time.monotonic() < deadline:
        output = tmux._capture_pane(pane)
        if marker in output:
            return output
        time.sleep(0.01)
    pytest.fail(f"Missing {marker!r} in {pane}: {output!r}")


def test_native_pane_sends_only_to_selected_pane(panes: dict[str, str]) -> None:
    message = tmux.send_keys(panes["first"], '"NATIVE_MARKER" Enter')
    assert message.content.startswith("Sent "), message.content
    wait_for(panes["first"], "NATIVE_MARKER")
    assert "NATIVE_MARKER" not in tmux._capture_pane(panes["second"])
    assert "NATIVE_MARKER" not in tmux._capture_pane(panes["outside"])


@pytest.mark.parametrize("target", ["1", "gptme_1", "gptme_1:0.0", "1:0.0"])
def test_session_shorthand_and_qualified_controls(
    panes: dict[str, str], target: str
) -> None:
    message = tmux.send_keys(target, "CONTROL_MARKER Enter")
    assert message.content.startswith("Sent "), message.content
    expected = panes["first"] if ":" in target else panes["second"]
    wait_for(expected, "CONTROL_MARKER")
    assert "CONTROL_MARKER" not in tmux._capture_pane(panes["outside"])


def test_native_pane_outside_namespace_is_rejected(panes: dict[str, str]) -> None:
    message = tmux.send_keys(panes["outside"], "FORBIDDEN_MARKER Enter")
    assert "outside the gptme_ session namespace" in message.content
    assert "FORBIDDEN_MARKER" not in tmux._capture_pane(panes["outside"])


def test_missing_native_pane_reports_original_target(panes: dict[str, str]) -> None:
    message = tmux.send_keys("%999999", "MISSING_MARKER Enter")
    assert "Failed" in message.content
    assert "`%999999`" in message.content
    assert "gptme_%999999" not in message.content


def test_native_pane_lookup_timeout_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(tmux.subprocess, "run", timeout)
    message = tmux.send_keys("%7", "never sent Enter")
    assert "timed out" in message.content
    assert "`%7`" in message.content
