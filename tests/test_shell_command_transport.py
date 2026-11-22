"""Quoted command envelopes preserve bash input and survive short writes."""

import os
import subprocess
import sys

import pytest

from gptme.tools.shell import ShellSession

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash transport")


@pytest.mark.parametrize(
    ("block", "encoding", "errors"),
    [
        ("printf '%s\\n' \"it's quoted\" '$HOME' '`false`' 'å☃'\n", "utf-8", "strict"),
        ("cat <<'EOF'\nquotes ' \" and $HOME and `false`\nEOF\n", "utf-8", "strict"),
        ("printf '%s' 'café'\n", "latin-1", "strict"),
        ("printf '%s' '\udcff'\n", "utf-8", "surrogateescape"),
        ("printf '%s' '☃'\n", "ascii", "replace"),
        ("printf '%s' 'before\x00after'\n", "utf-8", "strict"),
    ],
    ids=["quotes-unicode", "heredoc", "latin1", "non-utf8", "replace", "nul"],
)
def test_command_envelope_round_trip(block: str, encoding: str, errors: str) -> None:
    """Compare byte-for-byte with bash's existing raw-input semantics.

    In particular, NUL is not a newly supported bash input character: the
    envelope must preserve the behavior of the previous byte stream.
    """
    raw = subprocess.run(
        ["bash", "--noprofile", "--norc"],
        input=block.encode(encoding, errors),
        capture_output=True,
        timeout=5,
        check=False,
    )
    # Encoding is pure; no persistent shell or startup commands are needed.
    encoder = object.__new__(ShellSession)
    payload = encoder._encode_command_payload(block, encoding, errors)
    wrapped = subprocess.run(
        ["bash", "--noprofile", "--norc"],
        input=payload,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert (wrapped.returncode, wrapped.stdout, wrapped.stderr) == (
        raw.returncode,
        raw.stdout,
        raw.stderr,
    )
    assert raw.stdout  # These cases must actually exercise output, not just parse.


def test_command_envelope_strict_encoding_error() -> None:
    with pytest.raises(UnicodeEncodeError):
        object.__new__(ShellSession)._encode_command_payload(
            "echo ☃", "ascii", "strict"
        )


def test_large_multiline_envelope_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real persistent-shell writer across many partial deliveries."""
    shell = ShellSession()
    try:
        original_write = os.write
        assert shell.process.stdin is not None
        stdin_fd = shell.process.stdin.fileno()
        sizes: list[int] = []
        interrupted = False

        def short_write(fd: int, data: bytes) -> int:
            nonlocal interrupted
            if fd == stdin_fd:
                if not interrupted:
                    interrupted = True
                    raise InterruptedError
                written = original_write(fd, data[:257])
                sizes.append(written)
                return written
            return original_write(fd, data)

        monkeypatch.setattr(os, "write", short_write)
        pid = shell.process.pid
        # Exceed both PIPE_BUF and typical pipe capacity. Assignments survive
        # run()'s comment stripping without spawning external processes.
        block = (
            "TRANSPORT_PADDING='"
            + ("x" * 100 + "\n") * 2048
            + "'\n"
            + 'export TRANSPORT_VALUE="it\'s intact ☃"\n'
            + "cat </dev/null; cat\n"
            + "printf '%s\\n' \"$TRANSPORT_VALUE\"\n"
        )
        rc, out, err = shell.run(block, output=False, timeout=10)
        assert (rc, out, err) == (0, "it's intact ☃", "")
        assert interrupted and len(sizes) > 500
        assert sum(sizes) > 200_000
        assert max(sizes) <= 257
        assert shell.process.pid == pid
        assert shell.consume_restart_notice() is None
        assert shell.run("printf '%s' \"$TRANSPORT_VALUE\"", output=False, timeout=5)[
            :2
        ] == (0, "it's intact ☃")
    finally:
        shell.close()
