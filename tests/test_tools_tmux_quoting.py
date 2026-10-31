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


@pytest.mark.parametrize("keys", ["C-\\", "M-C-\\", "trailing\\", "'", '"'])
def test_dispatcher_preserves_literal_fallback_keys(
    monkeypatch: pytest.MonkeyPatch, keys: str
) -> None:
    calls: list[list[str]] = []

    def record_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tmux.subprocess, "run", record_run)
    monkeypatch.setattr(tmux, "_capture_pane", lambda _: "")
    monkeypatch.setattr(tmux, "sleep", lambda _: None)
    monkeypatch.setattr(tmux, "get_confirmation", ConfirmationResult.confirm)
    messages = list(tmux.execute_tmux(f"send-keys 1 {keys}", [], None))
    assert len(messages) == 1
    assert messages[0].content.startswith("Sent ")
    assert calls == [["tmux", "send-keys", "-t", "gptme_1", keys]]


@pytest.mark.parametrize("suffix", ['"unclosed', "'unclosed"])
def test_unmatched_quotes_keep_send_keys_fallback(
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
    assert calls == [("1", "ok Enter"), ("1", suffix)]
    assert len(messages) == 2


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


def test_adjacent_compound_comment_cannot_kill_another_session(
    private_tmux: None,
) -> None:
    messages = list(
        tmux.execute_tmux(
            "new-session (printf 'comment-ok\\n')# ignore; kill-session 1\nlist-sessions",
            [],
            None,
        )
    )
    assert not any("Killed tmux session" in message.content for message in messages)
    assert "gptme_1" in tmux.get_sessions()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "comment-ok" in tmux._capture_pane("gptme_2"):
            return
        time.sleep(0.01)
    pytest.fail("compound before adjacent comment did not execute")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('"$(printf \'"\')"', 'substitution-"'),
        ('"$(printf "\'")"', "substitution-'"),
        ('"$(printf "%s" "$(printf \'"\')")"', 'substitution-"'),
    ],
)
def test_new_session_preserves_nested_shell_quotes(
    private_tmux: None, expression: str, expected: str
) -> None:
    # Bash has independent quoting scopes inside command substitutions. The
    # dispatcher's simpler quote tracker must not reject valid shell syntax.
    command = f"printf 'substitution-%s\\n' {expression}"
    assert expected not in command
    messages = list(
        tmux.execute_tmux(None, None, {"command": f"new-session {command}"})
    )
    assert len(messages) == 1
    assert not messages[0].content.startswith("Error:")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if expected in tmux._capture_pane("gptme_2"):
            return
        time.sleep(0.01)
    pytest.fail("new-session did not execute the nested shell quotes")


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        ("(printf 'subshell-%s' LEFT; printf '_%s\\n' RIGHT)", "subshell-LEFT_RIGHT"),
        (
            "cat <<'END' | tr '[:lower:]' '[:upper:]'\n"
            'heredoc-marker; list-sessions\n"unmatched\nEND',
            'HEREDOC-MARKER; LIST-SESSIONS\n"UNMATCHED',
        ),
        ("cat <<-END\n\theredoc-$(printf '%s' expanded)\n\tEND", "heredoc-expanded"),
        (
            "if true; then (printf 'nested-%s' LEFT; printf '_%s\\n' RIGHT); fi",
            "nested-LEFT_RIGHT",
        ),
        (
            "for word in LEFT RIGHT; do printf '%s' \"$word\"; done",
            "LEFTRIGHT",
        ),
        (
            "{ printf 'redirected-%s' LEFT; printf '_%s\\n' RIGHT; } | cat",
            "redirected-LEFT_RIGHT",
        ),
    ],
)
def test_compound_executes_in_private_bash(
    private_tmux: None, shell: str, expected: str
) -> None:
    # The output differs from terminal echo, so completion requires real Bash.
    assert expected not in shell
    messages = list(tmux.execute_tmux(f"new-session {shell}\nlist-sessions", [], None))
    assert len(messages) == 2
    assert not any(message.content.startswith("Error:") for message in messages)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if expected in tmux._capture_pane("gptme_2"):
            return
        time.sleep(0.01)
    pytest.fail(f"compound did not execute: {tmux._capture_pane('gptme_2')!r}")


def test_case_pattern_substitution_keeps_outer_batch(private_tmux: None) -> None:
    command = (
        "new-session printf '%s-%s\\n' case "
        "\"$(case '((' in \\(\\() printf ok;; esac)\"; list-sessions"
    )
    assert "case-ok" not in command
    messages = list(tmux.execute_tmux(command, [], None))
    assert len(messages) == 2
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "case-ok" in tmux._capture_pane("gptme_2"):
            return
        time.sleep(0.01)
    pytest.fail("case substitution did not execute in the private shell")


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
        (
            'new-session echo "$(printf " # tag")"; list-sessions',
            ['echo "$(printf " # tag")"', "list"],
        ),
        (
            'new-session echo "$(printf "%s;inside" "$(printf " # tag")")"; list-sessions',
            ['echo "$(printf "%s;inside" "$(printf " # tag")")"', "list"],
        ),
        (
            "new-session echo $(printf hi; printf there); list-sessions",
            ["echo $(printf hi; printf there)", "list"],
        ),
        (
            'new-session echo "\\$(literal"; list-sessions',
            ['echo "\\$(literal"', "list"],
        ),
        (
            'new-session echo "$(case x in \\(\\() echo no;; esac)"; list-sessions',
            ['echo "$(case x in \\(\\() echo no;; esac)"', "list"],
        ),
        (
            "send-keys 1 C-\\\nlist-sessions",
            ["C-\\", "list"],
        ),
        (
            'send-keys 1 "λ" Enter; new-session echo "$(printf " # tag")"; list-sessions',
            ['"λ" Enter', 'echo "$(printf " # tag")"', "list"],
        ),
        (
            'new-session echo "$(case x in \\)) echo no;; *) echo yes;; esac)"; list-sessions',
            ['echo "$(case x in \\)) echo no;; *) echo yes;; esac)"', "list"],
        ),
        (
            'new-session echo "$( (printf "%s" ok) )"; list-sessions',
            ['echo "$( (printf "%s" ok) )"', "list"],
        ),
        (
            "send-keys 1 C-\\;list-sessions",
            ["C-\\;list-sessions"],
        ),
        (
            "send-keys 1 #literal Enter; new-session echo $(printf hi; printf there); list-sessions",
            ["#literal Enter", "echo $(printf hi; printf there)", "list"],
        ),
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
