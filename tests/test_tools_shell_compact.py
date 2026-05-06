from pathlib import Path
from unittest.mock import MagicMock, patch

from gptme.tools.shell_compact import (
    _format_git_log_preview,
    execute_shell_compact,
)


def _fixture_text(name: str) -> str:
    return (Path(__file__).parent / "data" / name).read_text(encoding="utf-8")


def test_format_git_log_preview_records_context_savings(tmp_path):
    stdout = _fixture_text("git-log-oneline.txt")

    preview = _format_git_log_preview("git log --oneline", stdout, tmp_path)

    assert preview is not None
    assert "Showing first 20 of 27 commits." in preview
    assert "more commits omitted" in preview
    assert "Full output saved to" in preview

    ledger = tmp_path / "context-savings.jsonl"
    rows = ledger.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert "shell_compact" in rows[0]
    assert "git_log_oneline: git log --oneline" in rows[0]


def test_execute_shell_compact_uses_compactor(tmp_path):
    stdout = _fixture_text("git-log-oneline.txt")

    with (
        patch("gptme.tools.shell_compact.get_shell") as mock_get_shell,
        patch("gptme.tools.shell_compact.get_path_fn", return_value=tmp_path),
    ):
        shell = MagicMock()
        shell.run.return_value = (0, stdout, "")
        mock_get_shell.return_value = shell

        messages = list(execute_shell_compact("git log --oneline", [], None))

    assert len(messages) == 1
    assert "Ran allowlisted compact command" in messages[0].content
    assert "Showing first 20 of 27 commits." in messages[0].content
    assert "tool-outputs/shell" in messages[0].content


def test_execute_shell_compact_falls_back_for_unsupported_command():
    with patch("gptme.tools.shell_compact.execute_shell") as mock_execute_shell:
        mock_execute_shell.return_value = iter([])

        list(execute_shell_compact("git status", [], None))

    mock_execute_shell.assert_called_once()
