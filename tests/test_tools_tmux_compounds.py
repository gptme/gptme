"""Shell units and explicit tmux batches have different separator grammars."""

import pytest

from gptme.hooks import ConfirmationResult
from gptme.message import Message
from gptme.tools import tmux


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def record(value: str) -> Message:
        calls.append(value)
        return Message("system", "recorded")

    monkeypatch.setattr(tmux, "get_confirmation", ConfirmationResult.confirm)
    monkeypatch.setattr(tmux, "new_session", record)
    monkeypatch.setattr(tmux, "send_keys", lambda pane, keys: record(keys))
    monkeypatch.setattr(tmux, "list_sessions", lambda: record("list"))
    return calls


@pytest.mark.parametrize(
    "shell",
    [
        "cat <<EOF\nbody; list-sessions\nEOF",
        "cat <<'END'\n\"unmatched; # body\nnew-session literal\nEND",
        'cat <<"END"\nbody; \'quoted\nEND',
        "cat <<-EOF\n\tλ; list-sessions\n\tEOF",
        "cat <<EOF | tr '[:lower:]' '[:upper:]'\nbody; text\nEOF",
        "(printf LEFT; printf RIGHT)",
        "(printf LEFT; (printf MIDDLE; printf RIGHT))",
        "{ printf LEFT; printf RIGHT; }",
        "if true; then\nprintf LEFT; printf RIGHT\nfi",
        "for word in LEFT RIGHT; do printf '%s' \"$word\"; done",
        "while false; do printf NEVER; done",
        "until true; do printf NEVER; done",
        "f() { printf LEFT; printf RIGHT; }",
        "{ printf LEFT; printf RIGHT; } >/dev/null",
        "case value in value) printf LEFT;; *) printf RIGHT;; esac",
        "printf '%s' <(printf LEFT; printf RIGHT)",
        "printf '%s' `printf LEFT; printf RIGHT`",
        "printf 'λ%s' \"$(printf '\"'; printf RIGHT)\"",
        "printf LEFT &&\nprintf RIGHT",
        "printf LEFT |\ncat",
        "printf '%s' \\\nLEFT",
    ],
)
def test_shell_unit_is_one_dispatch(shell: str, dispatched: list[str]) -> None:
    messages = list(tmux.execute_tmux(f"new-session {shell}\nlist-sessions", [], None))
    assert dispatched == [shell, "list"], [m.content for m in messages]


@pytest.mark.parametrize(
    "shell",
    [
        "(printf ok)# ignore; kill-session 1",
        "(printf 'λ')# ignore; kill-session 1",
        "(printf ok) # ignore; kill-session 1",
        "{ printf ok; } # ignore; kill-session 1",
    ],
)
def test_shell_comment_after_compound_is_not_a_tmux_command(
    shell: str, dispatched: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmux, "kill_session", lambda target: pytest.fail(target))
    messages = list(tmux.execute_tmux(f"new-session {shell}\nlist-sessions", [], None))
    assert dispatched == [shell, "list"], [m.content for m in messages]


@pytest.mark.parametrize("shell", ["printf x#literal", "printf $(printf x)#literal"])
def test_shell_word_suffix_hash_stays_literal(
    shell: str, dispatched: list[str]
) -> None:
    list(tmux.execute_tmux(f"new-session {shell}; list-sessions", [], None))
    assert dispatched == [shell, "list"]


def test_top_level_separators_are_tmux_not_shell(dispatched: list[str]) -> None:
    messages = list(
        tmux.execute_tmux(
            "new-session printf FIRST; printf NOT_SHELL; new-session printf SECOND",
            [],
            None,
        )
    )
    assert dispatched == ["printf FIRST", "printf SECOND"]
    assert any("Unknown command: printf" in m.content for m in messages)


@pytest.mark.parametrize(
    "shell",
    [
        "(printf PARTIAL; printf MORE",
        "cat <<EOF\nnever terminated",
        "if true; then printf PARTIAL",
        "printf 'unclosed",
        "printf x &&",
        "cat <<EOF\nbody\nEOF; list-sessions",
        "cat <<EOF\nbody\nEOF trailing",
        "cat <<EOF\nbody\nEOF   \nlist-sessions",
        "cat <<EOF\nbody\nEOF   ",
        "cat <<EOF\nbody\n EOF\nlist-sessions",
        "printf x >; list-sessions",
        "time { printf LEFT; printf RIGHT; }; list-sessions",
        'echo "$(case x in [)]) echo no;; *) echo yes;; esac)"',
    ],
)
def test_malformed_shell_refuses_whole_batch(shell: str, dispatched: list[str]) -> None:
    messages = list(tmux.execute_tmux(f"list-sessions; new-session {shell}", [], None))
    assert dispatched == [], [m.content for m in messages]
    assert len(messages) == 1
    assert "Error" in messages[0].content


def test_compound_followed_by_literal_keys(dispatched: list[str]) -> None:
    shell = "(printf LEFT; printf RIGHT)"
    list(
        tmux.execute_tmux(
            f"new-session {shell}; send-keys 1 C-\\\nlist-sessions", [], None
        )
    )
    assert dispatched == [shell, "C-\\", "list"]


@pytest.mark.parametrize("separator", ["\n", " \n", ";", " \t;"])
def test_missing_shell_argument_does_not_consume_next_verb(
    separator: str, dispatched: list[str]
) -> None:
    messages = list(
        tmux.execute_tmux(f"new-session{separator} list-sessions", [], None)
    )
    assert dispatched == ["list"]
    assert any(
        "Missing arguments for command: new-session" in m.content for m in messages
    )


def test_decline_still_prevents_dispatch(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[str]
) -> None:
    monkeypatch.setattr(tmux, "get_confirmation", ConfirmationResult.skip)
    messages = list(
        tmux.execute_tmux("new-session (printf LEFT; printf RIGHT)", [], None)
    )
    assert dispatched == []
    assert len(messages) == 1
    assert messages[0].content == tmux.DECLINED_CONTENT


def test_unicode_literal_keys_before_and_after_shell(dispatched: list[str]) -> None:
    shell = "(printf 'λ'; printf RIGHT)"
    list(
        tmux.execute_tmux(
            f'send-keys 1 "λ" Enter; new-session {shell}; send-keys 1 )', [], None
        )
    )
    assert dispatched == ['"λ" Enter', shell, ")"]


def test_invalid_second_shell_prevents_first_dispatch(dispatched: list[str]) -> None:
    messages = list(
        tmux.execute_tmux(
            "new-session printf FIRST; new-session (printf BROKEN", [], None
        )
    )
    assert dispatched == []
    assert len(messages) == 1
    assert "Error" in messages[0].content


@pytest.mark.parametrize("body", ["body\\", "body\\\\\\"])
def test_unquoted_heredoc_continuation_is_not_a_terminator(
    body: str, dispatched: list[str]
) -> None:
    messages = list(
        tmux.execute_tmux(
            f"list-sessions; new-session cat <<EOF\n{body}\nEOF\nlist-sessions",
            [],
            None,
        )
    )
    assert dispatched == []
    assert "Error" in messages[0].content


def test_quoted_delimiter_trailing_space_is_preserved(dispatched: list[str]) -> None:
    shell = "cat <<'EOF '\nbody\nEOF "
    list(tmux.execute_tmux(f"new-session {shell}\nlist-sessions", [], None))
    assert dispatched == [shell, "list"]


@pytest.mark.parametrize("separator", [";", "\n"])
@pytest.mark.parametrize("keys", ["| (", "| |", "(", '"unmatched', "C-\\"])
def test_later_literal_syntax_does_not_poison_shell(
    separator: str, keys: str, dispatched: list[str]
) -> None:
    list(
        tmux.execute_tmux(
            f"new-session echo ok{separator} send-keys 1 {keys}", [], None
        )
    )
    assert dispatched == ["echo ok", keys]


@pytest.mark.parametrize(
    "failure", ["missing", "timeout", "oserror", "status", "warning"]
)
def test_syntax_validation_failure_refuses_batch(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[str], failure: str
) -> None:
    import subprocess

    if failure == "missing":
        monkeypatch.setattr(tmux.shutil, "which", lambda name: None)
    else:

        def validate(
            *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired("bash", 10)
            if failure == "oserror":
                raise OSError("fixture unavailable")
            return subprocess.CompletedProcess(
                "bash", 2 if failure == "status" else 0, "", "fixture diagnostic"
            )

        monkeypatch.setattr(tmux.subprocess, "run", validate)
    messages = list(
        tmux.execute_tmux("list-sessions; new-session printf NEVER", [], None)
    )
    assert dispatched == []
    assert len(messages) == 1
    assert "Error" in messages[0].content
