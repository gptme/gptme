"""Returned shell evidence preserves boundaries emitted by split commands."""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from gptme.tools.shell import ShellSession, trim_blank_lines


@pytest.fixture
def shell(tmp_path: Path) -> Iterator[ShellSession]:
    session = ShellSession(cwd=str(tmp_path))
    try:
        yield session
    finally:
        session.close()


@pytest.mark.parametrize("reader", ["unix", "windows", "tty"])
@pytest.mark.parametrize("redirect", ["", " >&2"], ids=["stdout", "stderr"])
@pytest.mark.parametrize(
    "commands",
    [
        ["printf 'ONE\\n'", "printf 'TWO\\n'"],
        ["printf 'ONE\\n\\n'", "printf '\\nTWO\\n'"],
        ["printf 'ONE\\n'", "printf '\\n'", "printf 'TWO\\n'"],
        ["printf 'ONE\\n'", "true", "printf 'TWO\\n'"],
        ["printf 'ONE'", "printf 'TWO'"],
        ["printf 'ONE'", "printf '\\nTWO'"],
        ["printf '\\n\\n  ONE \\n'", "printf '\\n\\tTWO \\n\\n'"],
        ["true", "true"],
    ],
    ids=[
        "line-boundary",
        "blank-boundaries",
        "blank-only-command",
        "empty-command",
        "no-newline-adjacency",
        "leading-newline",
        "indentation-and-outer-trimming",
        "empty-output",
    ],
)
def test_multiline_output_matches_unsplit_bash(
    shell: ShellSession,
    commands: list[str],
    redirect: str,
    reader: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if reader == "windows":
        # Exercise the queue-based reader with real pipes on a Bash-capable host.
        monkeypatch.setattr(shell, "_read_output_unix", shell._read_output_windows)
    elif reader == "tty":
        monkeypatch.setattr(shell, "_needs_tty", lambda command: True)
    commands = [command + redirect for command in commands]
    script = "\n".join(commands)
    direct = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    expected = (
        direct.returncode,
        trim_blank_lines(direct.stdout),
        trim_blank_lines(direct.stderr),
    )
    # Both forms produce the same bytes; only one is split by ShellSession.run.
    assert shell.run("; ".join(commands), output=False) == expected
    assert shell.run(script, output=False) == expected
    assert shell.run("printf 'still-alive'", output=False) == (0, "still-alive", "")


def test_multiline_keeps_stdout_and_stderr_independent(shell: ShellSession) -> None:
    result = shell.run(
        "printf 'OUT\\n'; printf 'ERR\\n' >&2\nprintf '\\nNEXT'; printf '\\nLAST' >&2",
        output=False,
    )
    assert result == (0, "OUT\n\nNEXT", "ERR\n\nLAST")


def test_multiline_failure_preserves_output_and_stops(shell: ShellSession) -> None:
    result = shell.run(
        "printf 'BEFORE\\n'\n"
        "printf 'BEFORE-ERR\\n' >&2\n"
        "printf '\\nFAILED'; printf '\\nFAILED-ERR' >&2; false\n"
        "touch should-not-run",
        output=False,
    )
    assert result == (1, "BEFORE\n\nFAILED", "BEFORE-ERR\n\nFAILED-ERR")
    assert not (shell.get_cwd() / "should-not-run").exists()


@pytest.mark.parametrize("reader", ["unix", "tty"])
def test_multiline_timeout_preserves_boundaries_and_stops(
    shell: ShellSession, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    if reader == "tty":
        monkeypatch.setattr(shell, "_needs_tty", lambda command: True)
    result = shell.run(
        "printf 'BEFORE\\n'\n"
        "printf 'BEFORE-ERR\\n' >&2\n"
        "printf '\\nTIMEOUT'; printf '\\nTIMEOUT-ERR' >&2; sleep 10\n"
        "touch should-not-run",
        output=False,
        timeout=0.5,
    )
    assert result[0] == -124
    assert result[1] == "BEFORE\n\nTIMEOUT"
    # Bash may report the killed child on stderr; the user output stays intact.
    assert result[2].startswith("BEFORE-ERR\n\nTIMEOUT-ERR")
    assert not (shell.get_cwd() / "should-not-run").exists()
    assert shell.run("printf recovered", output=False) == (0, "recovered", "")
