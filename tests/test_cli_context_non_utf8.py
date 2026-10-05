"""gptme-util context: non-UTF-8 text files must not crash the commands."""

from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from gptme.cli.util import main


def test_context_tree_tolerates_latin1_gitignore(tmp_path: Path):
    (tmp_path / ".gitignore").write_bytes(b"# f\xf6r test\n*.log\n")
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "x.log").write_text("x")
    result = CliRunner().invoke(main, ["context", "tree", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "a.txt" in result.output
    assert "x.log" not in result.output


def test_context_journal_tolerates_latin1_entry(tmp_path: Path):
    today = datetime.now(tz=timezone.utc).astimezone().date().strftime("%Y-%m-%d")
    day = tmp_path / today
    day.mkdir()
    (day / "a.md").write_bytes(b"caf\xe9 notes\n")
    result = CliRunner().invoke(
        main, ["context", "journal", "--path", str(tmp_path), "--days", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "notes" in result.output
