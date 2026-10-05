"""gptme-util: non-UTF-8 input files produce clean errors, not tracebacks."""

from pathlib import Path

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
