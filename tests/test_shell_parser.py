"""Bash parser migration: source fidelity, boundaries, and compatibility."""

import shutil
import subprocess

import pytest

from gptme.tools.shell import _split_commands_bashlex, split_commands

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="Requires Bash")


@pytest.mark.parametrize(
    "command",
    [
        "[[ -f /etc/hostname ]] && echo yes",
        "echo $((6*7))",
        "case x in x) echo a;; esac",
        "time (\n echo a\n echo b\n)",
        "cat <<-'EOF'\n\tbody\n\tEOF",
        "diff <(ls) <(ls)",
        "coproc cat",
        "select x in a b; do echo $x; done",
        "for ((i=0;i<2;i++)); do echo $i; done",
    ],
)
def test_extended_grammar_splits_surrounding_commands(command: str) -> None:
    assert split_commands(f"echo before\n{command}\necho after") == [
        "echo before",
        command,
        "echo after",
    ]


@pytest.mark.parametrize(
    "script",
    [
        "echo a\necho b",
        "echo a; echo b\necho c",
        "sleep 1 &\necho done",
        "sleep 1 & echo done",
        "ls &&\n pwd\necho end",
        "ls |\n wc -l\necho end",
        "f() {\n echo hi\n}\nf",
        "(echo a\necho b)\necho c",
        "{ echo a\necho b; }\necho c",
        "for x in a b; do\n echo $x\ndone\necho c",
        "if true; then\n echo a\nfi\necho c",
        "echo héj\necho 世界",
        "cat <<'EOF' > out\nbody\nEOF\necho end",
        "# before\necho a # inline\n# between\necho b",
    ],
)
def test_bashlex_differential_corpus(script: str) -> None:
    assert split_commands(script) == _split_commands_bashlex(script)


def test_quoted_heredocs_preserve_source_and_expansion() -> None:
    # The old rewrite/restore path changes literal <<EOF inside a quoted body
    # and can also change an unquoted heredoc sharing the same delimiter.
    first = "cat << 'EOF'\nliteral <<EOF and $HOME\nEOF"
    second = "cat <<EOF\nexpanded $((6*7))\nEOF"
    assert split_commands(first + "\n" + second) == [first, second]
    outputs = [
        subprocess.run(
            ["bash"], input=command, text=True, capture_output=True, check=True
        ).stdout
        for command in split_commands(first + "\n" + second)
    ]
    assert outputs == ["literal <<EOF and $HOME\n", "expanded 42\n"]


@pytest.mark.parametrize(
    "script",
    [
        "time {\n echo a\n echo b\n}\necho done",
        "cat <<A <<B\na\nA\nb\nB\necho done",
    ],
)
def test_grammar_gaps_keep_complete_commands(script: str) -> None:
    commands = split_commands(script)
    assert commands == _split_commands_bashlex(script)
    for command in commands:
        subprocess.run(
            ["bash", "-n"], input=command, text=True, capture_output=True, check=True
        )


@pytest.mark.parametrize("script", ["echo 'unclosed", "ls |", "if true; then\necho x"])
def test_error_recovery_never_executes_partial_tree(script: str) -> None:
    with pytest.raises(ValueError, match="Shell syntax error"):
        split_commands(script)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo héj &", "echo héj < /dev/null &"),
        ("echo $((6*7)) &", "echo $((6*7)) < /dev/null &"),
        ("[[ -f x ]] && cat x &", "[[ -f x ]] && cat x < /dev/null &"),
        ("echo '&'", "echo '&'"),
        ("echo a && echo b", "echo a && echo b"),
        ("echo $((1 & 2))", "echo $((1 & 2))"),
        ("echo a 2>&1", "echo a 2>&1"),
        ("cat <<'EOF'\n&\nEOF", "cat <<'EOF'\n&\nEOF"),
    ],
)
def test_background_operator_grammar(command: str, expected: str) -> None:
    from gptme.tools.shell import _redirect_background_stdin

    assert _redirect_background_stdin(command) == expected


def test_logical_line_continuation_and_comment_boundaries() -> None:
    continued = "echo a; " + "\\\n" + "echo b"
    assert split_commands(continued + "\necho c") == [continued, "echo c"]
    commented = "echo a # comment " + "\\\n" + "echo b"
    assert split_commands(commented) == ["echo a", "echo b"]
