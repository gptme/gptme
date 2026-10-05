"""gptme-util tokens count: clean errors for non-text input."""

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
