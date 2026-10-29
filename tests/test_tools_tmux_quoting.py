"""Dispatcher quoting regressions against a private tmux server."""

import shlex
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from gptme.hooks import ConfirmationResult
from gptme.tools import tmux


@pytest.fixture
def private_tmux(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed")
    socket = f"gptme-quotes-{uuid.uuid4().hex}"
    run = subprocess.run

    def isolated_run(
        cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess:
        if cmd[0] == "tmux":
            cmd = ["tmux", "-L", socket, "-f", "/dev/null", *cmd[1:]]
        return run(cmd, *args, **kwargs)

    monkeypatch.setattr(tmux.subprocess, "run", isolated_run)
    monkeypatch.setattr(tmux, "sleep", lambda _: None)
    monkeypatch.setattr(tmux, "get_confirmation", ConfirmationResult.confirm)
    try:
        isolated_run(
            [
                "tmux",
                "new-session",
                "-d",
                "-x",
                "120",
                "-y",
                "40",
                "-s",
                "gptme_1",
                "stty -echo; cat",
            ],
            check=True,
            capture_output=True,
        )
        yield
    finally:
        isolated_run(["tmux", "kill-server"], capture_output=True)


def capture_until(expected: str) -> str:
    deadline = time.monotonic() + 3
    output = ""
    while time.monotonic() < deadline:
        output = tmux._capture_pane("gptme_1")
        if expected in output:
            return output
        time.sleep(0.01)
    pytest.fail(f"Expected {expected!r} in pane: {output!r}")


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (
            r'"before \"quoted; AFTER_MARKER\" done"',
            'before "quoted; AFTER_MARKER" done',
        ),
        (
            r'"before \\\"quoted; AFTER_MARKER\" done"',
            'before \\"quoted; AFTER_MARKER" done',
        ),
        ("'single \" quote; AFTER_MARKER'", 'single " quote; AFTER_MARKER'),
        ('"double \' quote; AFTER_MARKER"', "double ' quote; AFTER_MARKER"),
        ('"quoted\nAFTER_MARKER"', "quoted\nAFTER_MARKER"),
        (r"escaped\;AFTER_MARKER", "escaped;AFTER_MARKER"),
        (r"\"literal; send-keys gptme_1 AFTER_MARKER Enter", None),
    ],
)
def test_dispatcher_preserves_quoted_text(
    private_tmux: None, keys: str, expected: str | None
) -> None:
    messages = list(
        tmux.execute_tmux(None, None, {"command": f"send-keys gptme_1 {keys} Enter"})
    )
    # An escaped quote outside quotes is literal, not an opening delimiter.
    if expected is None:
        assert len(messages) == 2
        capture_until("AFTER_MARKER")
        return
    assert len(messages) == 1, [message.content for message in messages]
    assert messages[0].content.startswith("Sent ")
    # With terminal echo disabled, cat emits the text only after Enter.
    capture_until(expected)


@pytest.mark.parametrize("separator", [";", "\n"])
def test_intentional_command_separators_and_named_keys(
    private_tmux: None, separator: str
) -> None:
    command = separator.join(
        [
            'send-keys gptme_1 "one; intact" Enter',
            'send-keys gptme_1 "two" BSpace "THREE" Enter',
        ]
    )
    messages = list(tmux.execute_tmux(command, [], None))
    assert len(messages) == 2
    capture_until("twTHREE")
    assert "one; intact" in tmux._capture_pane("gptme_1")


@pytest.mark.parametrize("suffix", ['"unclosed', "'unclosed", "trailing\\"])
def test_malformed_input_has_no_partial_dispatch(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        tmux, "send_keys", lambda pane, keys: calls.append((pane, keys))
    )
    monkeypatch.setattr(tmux, "get_confirmation", ConfirmationResult.confirm)
    messages = list(
        tmux.execute_tmux(f"send-keys 1 ok Enter; send-keys 1 {suffix}", [], None)
    )
    assert not calls
    assert len(messages) == 1
    assert "Error:" in messages[0].content


@pytest.mark.parametrize(
    "comment", ["don't wait", 'say "hello', "trailing\\", "ignore; send-keys 1 bad"]
)
def test_new_session_preserves_shell_comments(private_tmux: None, comment: str) -> None:
    messages = list(
        tmux.execute_tmux(
            None,
            None,
            {"command": f"new-session printf 'comment-%s\\n' ok # {comment}"},
        )
    )
    assert len(messages) == 1
    assert not messages[0].content.startswith("Error:")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "comment-ok" in tmux._capture_pane("gptme_2"):
            return
        time.sleep(0.01)
    pytest.fail("new-session did not execute the shell command before its comment")


def test_new_session_keeps_shell_quoting(private_tmux: None) -> None:
    # The completed output is absent from the submitted command, so terminal echo
    # cannot satisfy the assertion before the shell executes printf.
    command = "printf 'new %s\\n' " + shlex.quote('"quoted; AFTER_MARKER"')
    assert 'new "quoted; AFTER_MARKER"' not in command
    messages = list(
        tmux.execute_tmux(None, None, {"command": f"new-session {command}"})
    )
    assert len(messages) == 1
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if 'new "quoted; AFTER_MARKER"' in tmux._capture_pane("gptme_2"):
            break
        time.sleep(0.01)
    else:
        pytest.fail("new-session did not preserve quoted command")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "new-session echo ok # don't wait\nlist-sessions",
            ["echo ok # don't wait", "list"],
        ),
        ("new-session\techo ok # don't wait", ["echo ok # don't wait"]),
        ("new-session echo word#tag", ["echo word#tag"]),
        (r"new-session echo \#tag", [r"echo \#tag"]),
        (r"new-session echo word\ #tag", [r"echo word\ #tag"]),
        (r"new-session echo word\|#tag", [r"echo word\|#tag"]),
        ('new-session echo "#tag"', ['echo "#tag"']),
        ("send-keys 1 #literal Enter", ["#literal Enter"]),
    ],
)
def test_comment_boundaries_preserve_dispatch(
    monkeypatch: pytest.MonkeyPatch, command: str, expected: list[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tmux, "get_confirmation", ConfirmationResult.confirm)
    monkeypatch.setattr(tmux, "new_session", lambda value: calls.append(value))
    monkeypatch.setattr(tmux, "send_keys", lambda pane, value: calls.append(value))
    monkeypatch.setattr(tmux, "list_sessions", lambda: calls.append("list"))
    list(tmux.execute_tmux(command, [], None))
    assert calls == expected
