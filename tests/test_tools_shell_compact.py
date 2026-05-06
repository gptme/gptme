from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.base import ToolUse
from gptme.tools.shell_compact import (
    _compact_body_via_save,
    _compact_command_display,
    _dispatch_compactor,
    _execute_compacted,
    _format_gh_list_preview,
    _format_git_log_preview,
    _get_timeout,
    _matches_gh_list,
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


def test_execute_compacted_falls_back_when_command_fails(tmp_path):
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

        messages = list(_execute_compacted("git log --oneline", tmp_path, 7.5))

    assert messages == [Message("system", "raw shell output")]
    mock_format.assert_called_once()
    assert mock_format.call_args.kwargs["allowlisted"] is True
    assert mock_format.call_args.kwargs["timeout_value"] == 7.5
    assert mock_format.call_args.kwargs["logdir"] == tmp_path


def test_execute_compacted_falls_back_when_timed_out(tmp_path):
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

        messages = list(_execute_compacted("git log --oneline", tmp_path, 1.0))

    assert messages == [Message("system", "timed out output")]
    assert mock_format.call_args.kwargs["timed_out"] is True


def test_execute_compacted_falls_back_when_output_save_fails(tmp_path):
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

        messages = list(_execute_compacted("git log --oneline", tmp_path, 7.5))

    assert messages == [Message("system", "raw shell output")]
    mock_format.assert_called_once()
    assert mock_format.call_args.kwargs["allowlisted"] is True
    assert mock_format.call_args.args[3] == 0


def test_execute_compacted_keeps_preview_when_telemetry_fails(tmp_path):
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

        messages = list(_execute_compacted("git log --oneline", tmp_path, 7.5))

    assert len(messages) == 1
    assert "Ran allowlisted compact command" in messages[0].content
    assert "Showing first 20 of 27 commits." in messages[0].content
    assert "Full output saved to" in messages[0].content
    mock_format.assert_not_called()


def test_execute_compacted_raises_value_error_on_shell_error(tmp_path):
    with patch("gptme.tools.shell_compact.get_shell") as mock_get_shell:
        shell = MagicMock()
        shell.run.side_effect = RuntimeError("boom")
        mock_get_shell.return_value = shell

        with pytest.raises(ValueError, match="Shell error: boom"):
            list(_execute_compacted("git log --oneline", tmp_path, 1.0))


def test_execute_compacted_interrupts_process_group(tmp_path):
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

        messages = _execute_compacted("git log --oneline", tmp_path, 1.0)
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


# ── gh_list compactor tests ────────────────────────────────────────────────


def test_matches_gh_list_accepts_issue_list():
    assert _matches_gh_list("gh issue list --repo gptme/gptme") is True


def test_matches_gh_list_accepts_pr_list():
    assert _matches_gh_list("gh pr list --state open") is True
    assert _matches_gh_list("gh pr list --author TimeToBuildBob --limit 50") is True


def test_matches_gh_list_accepts_pr_status():
    assert _matches_gh_list("gh pr status") is True


def test_matches_gh_list_accepts_run_list():
    assert _matches_gh_list("gh run list --limit 10") is True


def test_matches_gh_list_accepts_release_list():
    assert _matches_gh_list("gh release list") is True


def test_matches_gh_list_accepts_repo_list():
    assert _matches_gh_list("gh repo list gptme --limit 100") is True


@pytest.mark.parametrize(
    "cmd",
    [
        "gh issue view 123",
        "gh pr checkout 456",
        "gh run watch 789",
        "gh pr create",
        "gh issue close 123",
        "gh --version",
        "ls",
        "git log",
        "git status",
    ],
)
def test_matches_gh_list_rejects_non_list_commands(cmd):
    assert _matches_gh_list(cmd) is False


def test_matches_gh_list_rejects_shell_chars():
    assert _matches_gh_list("gh issue list; rm -rf /") is False
    assert _matches_gh_list("gh issue list | grep foo") is False
    assert _matches_gh_list("gh issue list$(id)") is False
    assert _matches_gh_list("gh issue list `id`") is False


def test_format_gh_list_preview_records_context_savings(tmp_path):
    stdout = _fixture_text("gh-issue-list.txt")

    preview = _format_gh_list_preview(
        "gh issue list --repo gptme/gptme", stdout, tmp_path
    )

    assert preview is not None
    assert "Showing first 20 of 24 items." in preview
    assert "more items omitted" in preview
    assert "Full output saved to" in preview
    assert "pipe to `grep` to narrow" in preview

    ledger = tmp_path / "context-savings.jsonl"
    rows = ledger.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert "shell_compact" in rows[0]
    assert "gh_list:" in rows[0]


def test_format_gh_list_preview_skips_short_lists(tmp_path):
    stdout = "\n".join(_fixture_text("gh-issue-list.txt").splitlines()[:5])

    preview = _format_gh_list_preview("gh issue list", stdout, tmp_path)

    assert preview is None
    assert not (tmp_path / "context-savings.jsonl").exists()


def test_format_gh_list_preview_without_logdir(tmp_path):
    stdout = _fixture_text("gh-issue-list.txt")

    preview = _format_gh_list_preview("gh issue list", stdout, None)

    assert preview is not None
    assert (
        "Full output was not saved because no conversation logdir is active." in preview
    )
    assert "pipe to `grep` to narrow" in preview


def test_dispatch_compactor_routes_gh_list(tmp_path):
    stdout = _fixture_text("gh-issue-list.txt")
    result = _dispatch_compactor("gh issue list --repo gptme/gptme", stdout, tmp_path)
    assert result is not None
    assert "Showing first 20 of 24 items." in result


def test_dispatch_compactor_routes_git_log(tmp_path):
    stdout = _fixture_text("git-log-oneline.txt")
    result = _dispatch_compactor("git log --oneline", stdout, tmp_path)
    assert result is not None
    assert "Showing first 20 of 27 commits." in result


def test_dispatch_compactor_returns_none_for_unknown(tmp_path):
    result = _dispatch_compactor("cat README.md", "some content\n", tmp_path)
    assert result is None


def test_execute_shell_compact_uses_gh_list_compactor(tmp_path):
    stdout = _fixture_text("gh-issue-list.txt")

    with (
        patch("gptme.tools.shell_compact.get_shell") as mock_get_shell,
        patch("gptme.tools.shell_compact.get_path_fn", return_value=tmp_path),
    ):
        shell = MagicMock()
        shell.run.return_value = (0, stdout, "")
        mock_get_shell.return_value = shell

        messages = list(
            execute_shell_compact("gh issue list --repo gptme/gptme", [], None)
        )

    assert len(messages) == 1
    assert "Ran allowlisted compact command" in messages[0].content
    assert "Showing first 20 of 24 items." in messages[0].content


def test_shell_compact_allowlist_hook_confirms_gh_list():
    """The allowlist hook should auto-confirm gh list commands."""
    result = shell_compact_allowlist_hook(
        ToolUse("shell_compact", [], "gh issue list --repo gptme/gptme")
    )
    assert result is not None


def test_shell_compact_allowlist_hook_rejects_non_list():
    assert (
        shell_compact_allowlist_hook(ToolUse("shell_compact", [], "gh issue view 123"))
        is None
    )


def test_compact_body_via_save_short_output(tmp_path):
    """Short output returns None (no compacting needed)."""
    result = _compact_body_via_save(
        "echo hello", "hello\nworld\n", tmp_path, "test", "lines", ""
    )
    assert result is None


def test_compact_body_via_save_uses_provided_logdir(tmp_path):
    lines = "\n".join(f"line {i}" for i in range(30))
    result = _compact_body_via_save(
        "cat data.txt",
        lines,
        tmp_path,
        "test",
        "lines",
        "Use `shell` for full output.",
    )
    assert result is not None
    assert "Showing first 20 of 30 lines." in result
    assert "Full output saved to" in result
    assert "Use `shell` for full output." in result
