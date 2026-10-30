"""Literal pipes must not become operators during pipeline stdin protection."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from gptme.tools.shell import ShellSession
from gptme.tools.shell_validation import _find_first_unquoted_pipe


@pytest.mark.parametrize("backslashes", range(1, 6))
def test_pipe_location_respects_escape_parity(backslashes: int) -> None:
    command = "printf '%s' a" + "\\" * backslashes + "| cat | cat"
    first_pipe = command.index("|")
    expected = command.rindex("|") if backslashes % 2 else first_pipe
    assert _find_first_unquoted_pipe(command) == expected


def test_only_escaped_pipes_have_no_operator() -> None:
    assert _find_first_unquoted_pipe(r"printf '%s' a\|b\|c") is None


@pytest.mark.parametrize(
    "command",
    [
        r"printf '%s\n' a\|b | cat",
        r"printf '%s\n' a\|b\|c | cat",
        r"printf '%s\n' a\\\|b | cat",
        r"printf '%s\n' a\\ | cat",
        r"printf '%s\n' a\|| cat",
        r"printf '%s\n' 'a|b' | cat",
        r'printf "%s\n" "a|b" | cat',
        r"false || printf '%s\n' a\|b | cat",
        r"printf '%s\n' ordinary | cat",
        r"printf '%s\n' a\|b | sh -c 'cat; exit 7'",
        r"cat < input.txt | cat",
        "cat <<'EOF' | cat\na|b\nEOF",
        "cat <<< 'a|b' | cat",
        "cat | cat",
    ],
)
def test_pipeline_matches_bash(command: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "input.txt").write_text("explicit input\n")
        expected = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", command],
            input="",
            text=True,
            capture_output=True,
            check=False,
            cwd=directory,
            timeout=5,
        )
        shell = ShellSession(cwd=directory)
        try:
            actual = shell.run(command, output=False, timeout=5)
            assert actual == (
                expected.returncode,
                expected.stdout.rstrip("\n"),
                expected.stderr.rstrip("\n"),
            )
            # Stdin consumers must not eat the persistent shell's next command.
            assert shell.run("printf followup", output=False, timeout=5) == (
                0,
                "followup",
                "",
            )
        finally:
            shell.close()
