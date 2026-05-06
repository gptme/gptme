from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.base import ToolUse
from gptme.tools.shell_compact import (
    _compact_command_display,
    _execute_compacted_git_log,
    _format_git_log_preview,
    _get_timeout,
    _matches_git_log_oneline,
    execute_shell_compact,
    shell_compact_allowlist_hook,
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


def test_format_git_log_preview_skips_short_logs(tmp_path):
    stdout = "\n".join(_fixture_text("git-log-oneline.txt").splitlines()[:3])

    preview = _format_git_log_preview("git log --oneline", stdout, tmp_path)

    assert preview is None
    assert not (tmp_path / "context-savings.jsonl").exists()


def test_format_git_log_preview_without_logdir_does_not_save():
    stdout = _fixture_text("git-log-oneline.txt")

    preview = _format_git_log_preview("git log --oneline", stdout, None)

    assert preview is not None
    assert (
        "Full output was not saved because no conversation logdir is active." in preview
    )
    assert "more commits omitted" in preview


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


def test_execute_compacted_git_log_falls_back_when_command_fails(tmp_path):
    with (
        patch("gptme.tools.shell_compact.get_shell") as mock_get_shell,
        patch(
            "gptme.tools.shell_compact._format_shell_output",
            return_value="raw shell output",
        ) as mock_format,
    ):
        shell = MagicMock()
        shell.run.return_value = (1, "", "fatal: not a git repository")
        mock_get_shell.return_value = shell

        messages = list(_execute_compacted_git_log("git log --oneline", tmp_path, 7.5))

    assert messages == [Message("system", "raw shell output")]
    mock_format.assert_called_once()
    assert mock_format.call_args.kwargs["allowlisted"] is True
    assert mock_format.call_args.kwargs["timeout_value"] == 7.5
    assert mock_format.call_args.kwargs["logdir"] == tmp_path


def test_execute_compacted_git_log_falls_back_when_timed_out(tmp_path):
    with (
        patch("gptme.tools.shell_compact.get_shell") as mock_get_shell,
        patch(
            "gptme.tools.shell_compact._format_shell_output",
            return_value="timed out output",
        ) as mock_format,
    ):
        shell = MagicMock()
        shell.run.return_value = (-124, "partial", "")
        mock_get_shell.return_value = shell

        messages = list(_execute_compacted_git_log("git log --oneline", tmp_path, 1.0))

    assert messages == [Message("system", "timed out output")]
    assert mock_format.call_args.kwargs["timed_out"] is True


def test_execute_compacted_git_log_falls_back_when_output_save_fails(tmp_path):
    stdout = _fixture_text("git-log-oneline.txt")

    with (
        patch("gptme.tools.shell_compact.get_shell") as mock_get_shell,
        patch(
            "gptme.tools.shell_compact._format_shell_output",
            return_value="raw shell output",
        ) as mock_format,
        patch(
            "gptme.tools.shell_compact.save_large_output",
            side_effect=OSError("disk full"),
        ),
    ):
        shell = MagicMock()
        shell.run.return_value = (0, stdout, "")
        mock_get_shell.return_value = shell

        messages = list(_execute_compacted_git_log("git log --oneline", tmp_path, 7.5))

    assert messages == [Message("system", "raw shell output")]
    mock_format.assert_called_once()
    assert mock_format.call_args.kwargs["allowlisted"] is True
    assert mock_format.call_args.args[3] == 0


def test_execute_compacted_git_log_keeps_preview_when_telemetry_fails(tmp_path):
    stdout = _fixture_text("git-log-oneline.txt")

    with (
        patch("gptme.tools.shell_compact.get_shell") as mock_get_shell,
        patch("gptme.tools.shell_compact._format_shell_output") as mock_format,
        patch(
            "gptme.tools.shell_compact.record_context_savings",
            side_effect=OSError("ledger write failed"),
        ),
    ):
        shell = MagicMock()
        shell.run.return_value = (0, stdout, "")
        mock_get_shell.return_value = shell

        messages = list(_execute_compacted_git_log("git log --oneline", tmp_path, 7.5))

    assert len(messages) == 1
    assert "Ran allowlisted compact command" in messages[0].content
    assert "Showing first 20 of 27 commits." in messages[0].content
    assert "Full output saved to" in messages[0].content
    mock_format.assert_not_called()


def test_execute_compacted_git_log_raises_value_error_on_shell_error(tmp_path):
    with patch("gptme.tools.shell_compact.get_shell") as mock_get_shell:
        shell = MagicMock()
        shell.run.side_effect = RuntimeError("boom")
        mock_get_shell.return_value = shell

        with pytest.raises(ValueError, match="Shell error: boom"):
            list(_execute_compacted_git_log("git log --oneline", tmp_path, 1.0))


def test_execute_compacted_git_log_interrupts_process_group(tmp_path):
    process = MagicMock()
    process.pid = 123
    process.returncode = 130

    with (
        patch("gptme.tools.shell_compact.get_shell") as mock_get_shell,
        patch("gptme.tools.shell.os.getpgid", return_value=456) as mock_getpgid,
        patch("gptme.tools.shell.os.killpg") as mock_killpg,
        patch(
            "gptme.tools.shell_compact._format_shell_output",
            return_value="interrupted output",
        ),
    ):
        shell = MagicMock()
        shell.process = process
        shell.run.side_effect = KeyboardInterrupt(("partial stdout", "partial stderr"))
        mock_get_shell.return_value = shell

        messages = _execute_compacted_git_log("git log --oneline", tmp_path, 1.0)
        assert next(messages) == Message("system", "interrupted output")
        with pytest.raises(KeyboardInterrupt):
            next(messages)

    mock_getpgid.assert_called_once_with(123)
    mock_killpg.assert_called_once()


def test_execute_shell_compact_falls_back_for_unsupported_command():
    with patch("gptme.tools.shell_compact.execute_shell") as mock_execute_shell:
        mock_execute_shell.return_value = iter([])

        list(execute_shell_compact("git status", [], None))

    mock_execute_shell.assert_called_once()


@pytest.mark.parametrize(
    "cmd",
    [
        "git log --oneline",
        "git log --decorate --oneline -n 5",
        "git log '--oneline'",
    ],
)
def test_matches_git_log_oneline_accepts_supported_shapes(cmd):
    assert _matches_git_log_oneline(cmd) is True


@pytest.mark.parametrize(
    "cmd",
    [
        "git status --oneline",
        "git log",
        "git log --oneline | cat",
        "git log --oneline; pwd",
        "git log --oneline\npwd",
        "git log --oneline > out.txt",
        "git log '--oneline",
        "git log --oneline $(id)",
        "git log --oneline `id`",
        "git log --oneline $HOME",
    ],
)
def test_matches_git_log_oneline_rejects_unsupported_shapes(cmd):
    assert _matches_git_log_oneline(cmd) is False


def test_shell_compact_allowlist_hook_rejects_non_matching_tool_uses():
    assert (
        shell_compact_allowlist_hook(ToolUse("shell", [], "git log --oneline", {}))
        is None
    )
    assert shell_compact_allowlist_hook(ToolUse("shell_compact", [], "", {})) is None
    assert (
        shell_compact_allowlist_hook(ToolUse("shell_compact", [], "git status", {}))
        is None
    )


def test_get_timeout_reads_environment(monkeypatch):
    monkeypatch.setenv("GPTME_SHELL_TIMEOUT", "2.5")
    assert _get_timeout() == 2.5

    monkeypatch.setenv("GPTME_SHELL_TIMEOUT", "0")
    assert _get_timeout() is None

    monkeypatch.setenv("GPTME_SHELL_TIMEOUT", "not-a-number")
    assert _get_timeout() == 1200.0


def test_compact_command_display_shortens_long_or_multiline_commands():
    assert _compact_command_display("git log --oneline") == "git log --oneline"

    long_cmd = "git log --oneline " + ("--decorate " * 10)
    assert _compact_command_display(long_cmd).endswith("... (1 line)")

    multiline_cmd = "git log --oneline\npwd"
    assert _compact_command_display(multiline_cmd) == "git log --oneline... (2 lines)"
