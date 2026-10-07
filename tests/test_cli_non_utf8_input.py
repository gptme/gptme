"""gptme-util: non-UTF-8 input files produce clean errors, not tracebacks."""

import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.util import main


def test_tokens_count_binary_file_is_clean_error(tmp_path: Path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x80 not utf-8")
    result = CliRunner().invoke(main, ["tokens", "count", "-f", str(f)])
    assert result.exit_code == 1
    assert "not valid UTF-8" in result.output
    assert not isinstance(result.exception, UnicodeDecodeError)


def test_tokens_count_binary_stdin_is_clean_error():
    result = CliRunner().invoke(
        main, ["tokens", "count", "-f", "-"], input=b"\xff\xfe\x80 bad"
    )
    assert result.exit_code == 1
    assert "stdin is not valid UTF-8" in result.output


@pytest.mark.parametrize("args", [["-f", "-"], ["-"]])
@pytest.mark.parametrize(
    "encoding", ["utf-8:surrogateescape", "cp1252:surrogateescape"]
)
@pytest.mark.parametrize("valid", [False, True])
def test_tokens_count_real_stdin(args: list[str], encoding: str, valid: bool):
    payload = "héllo 世界\r\n".encode() if valid else b"\xff\xfe\x80 bad"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gptme.cli.util import main; main()",
            "tokens",
            "count",
            *args,
        ],
        input=payload,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": encoding},
        timeout=30,
    )
    assert b"Traceback" not in result.stderr
    if valid:
        from gptme.util.tokens import len_tokens

        assert result.returncode == 0, result.stderr
        expected = len_tokens(payload.decode("utf-8"), "gpt-4")
        assert f"Token count (gpt-4): {expected}".encode() in result.stdout
    else:
        assert result.returncode == 1
        assert b"stdin is not valid UTF-8" in result.stderr


@pytest.mark.parametrize("valid", [False, True])
def test_tokens_count_file_ignores_locale(tmp_path: Path, monkeypatch, valid: bool):
    f = tmp_path / "input.txt"
    payload = "héllo 世界".encode() if valid else b"\xff\xfe\x80 bad"
    f.write_bytes(payload)
    original_open = builtins.open

    def locale_open(path, *args, **kwargs):
        if path == str(f):
            kwargs.setdefault("encoding", "cp1252")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", locale_open)
    result = CliRunner().invoke(main, ["tokens", "count", "-f", str(f)])
    if valid:
        from gptme.util.tokens import len_tokens

        assert result.exit_code == 0, result.output
        expected = len_tokens(payload.decode("utf-8"), "gpt-4")
        assert f"Token count (gpt-4): {expected}" in result.output
    else:
        assert result.exit_code == 1
        assert "not valid UTF-8" in result.output


def test_tokens_count_text_still_works(tmp_path: Path):
    f = tmp_path / "t.txt"
    f.write_text("hello world")
    result = CliRunner().invoke(main, ["tokens", "count", "-f", str(f)])
    assert result.exit_code == 0
    assert "Token count" in result.output


def test_attest_verify_binary_file_is_clean_error(tmp_path: Path):
    from gptme.cli.cmd_attest import attest

    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x80 not utf-8")
    result = CliRunner().invoke(attest, ["verify", str(f)])
    assert result.exit_code == 1
    assert "Invalid attestation JSON" in result.output
    assert not isinstance(result.exception, UnicodeDecodeError)


def test_capabilities_from_json_binary_file_is_clean_error(tmp_path: Path):
    from gptme.cli.cmd_capabilities import capabilities

    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x80 not utf-8")
    result = CliRunner().invoke(capabilities, ["--from-json", str(f)])
    assert result.exit_code == 1
    assert "invalid JSON" in result.output
    assert not isinstance(result.exception, UnicodeDecodeError)
