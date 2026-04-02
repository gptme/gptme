"""Tests for gptme-util hooks subcommands (install, run, status, uninstall)."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

import gptme.cli.cmd_hooks as cmd_hooks
from gptme.cli.cmd_hooks import (
    _extract_pretooluse_text,
    _is_gptme_entry,
    _is_hook_installed,
)
from gptme.cli.util import main

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A temporary workspace directory with a minimal gptme.toml."""
    (tmp_path / "gptme.toml").write_text("[agent]\nname = 'Test'\n")
    return tmp_path


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect _STATE_DIR to a per-test tmp directory to prevent cross-test interference."""
    monkeypatch.setattr(cmd_hooks, "_STATE_DIR", tmp_path / "hook-state")


# ---------------------------------------------------------------------------
# _is_gptme_entry / _is_hook_installed helpers
# ---------------------------------------------------------------------------


def test_is_gptme_entry_detects_hook_command() -> None:
    entry = {"hooks": [{"type": "command", "command": "gptme-util hooks run"}]}
    assert _is_gptme_entry(entry)


def test_is_gptme_entry_ignores_unrelated() -> None:
    entry = {"hooks": [{"type": "command", "command": "python3 other_script.py"}]}
    assert not _is_gptme_entry(entry)


def test_is_hook_installed_true_when_present() -> None:
    cfg = {
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "gptme-util hooks run"}]}
        ]
    }
    assert _is_hook_installed(cfg, "UserPromptSubmit")


def test_is_hook_installed_false_when_absent() -> None:
    assert not _is_hook_installed({}, "UserPromptSubmit")
    cfg = {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}]}
    assert not _is_hook_installed(cfg, "UserPromptSubmit")


# ---------------------------------------------------------------------------
# hooks install
# ---------------------------------------------------------------------------


def test_install_creates_settings_file(runner: CliRunner, workspace: Path) -> None:
    result = runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    settings_path = workspace / ".claude" / "settings.json"
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    assert "hooks" in settings
    assert "UserPromptSubmit" in settings["hooks"]
    assert "PreToolUse" in settings["hooks"]


def test_install_registers_correct_command(runner: CliRunner, workspace: Path) -> None:
    runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    settings = json.loads((workspace / ".claude" / "settings.json").read_text())
    # UserPromptSubmit hook
    ups_entries = settings["hooks"]["UserPromptSubmit"]
    assert len(ups_entries) == 1
    cmd = ups_entries[0]["hooks"][0]["command"]
    assert cmd == "gptme-util hooks run"
    # PreToolUse hook has a matcher
    ptu_entries = settings["hooks"]["PreToolUse"]
    assert len(ptu_entries) == 1
    assert "matcher" in ptu_entries[0]


def test_install_idempotent(runner: CliRunner, workspace: Path) -> None:
    runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    result = runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "already present" in result.output
    # Settings file should still have exactly one hook entry each
    settings = json.loads((workspace / ".claude" / "settings.json").read_text())
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1
    assert len(settings["hooks"]["PreToolUse"]) == 1


def test_install_force_overwrites(runner: CliRunner, workspace: Path) -> None:
    runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    result = runner.invoke(
        main, ["hooks", "install", "--workspace", str(workspace), "--force"]
    )
    assert result.exit_code == 0, result.output
    # Should still be exactly one entry after force
    settings = json.loads((workspace / ".claude" / "settings.json").read_text())
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1


def test_install_merges_with_existing_hooks(runner: CliRunner, workspace: Path) -> None:
    """Existing non-gptme hooks should be preserved."""
    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    existing_settings = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "other_hook.py"}]}
            ]
        }
    }
    settings_path.write_text(json.dumps(existing_settings))
    runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    settings = json.loads(settings_path.read_text())
    entries = settings["hooks"]["UserPromptSubmit"]
    # Both hooks present: the original + the new gptme one
    assert len(entries) == 2
    commands = [e["hooks"][0]["command"] for e in entries]
    assert "other_hook.py" in commands
    assert "gptme-util hooks run" in commands


def test_install_partial_install_completed(runner: CliRunner, workspace: Path) -> None:
    """If only UserPromptSubmit is present, install should add the missing PreToolUse."""
    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    # Pre-install with only UserPromptSubmit hook
    partial = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "gptme-util hooks run"}]}
            ]
        }
    }
    settings_path.write_text(json.dumps(partial))
    result = runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    settings = json.loads(settings_path.read_text())
    # Both hooks should now be present, with exactly one entry each (no duplicates)
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1
    assert len(settings["hooks"]["PreToolUse"]) == 1


def test_install_no_gptme_toml_fails(runner: CliRunner, tmp_path: Path) -> None:
    """Without gptme.toml and without --force, install should exit non-zero."""
    result = runner.invoke(main, ["hooks", "install", "--workspace", str(tmp_path)])
    assert result.exit_code != 0


def test_install_no_gptme_toml_force_succeeds(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = runner.invoke(
        main, ["hooks", "install", "--workspace", str(tmp_path), "--force"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".claude" / "settings.json").exists()


# ---------------------------------------------------------------------------
# hooks uninstall
# ---------------------------------------------------------------------------


def test_uninstall_removes_hooks(runner: CliRunner, workspace: Path) -> None:
    runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    result = runner.invoke(main, ["hooks", "uninstall", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    settings = json.loads((workspace / ".claude" / "settings.json").read_text())
    assert not _is_hook_installed(settings.get("hooks", {}), "UserPromptSubmit")
    assert not _is_hook_installed(settings.get("hooks", {}), "PreToolUse")


def test_uninstall_preserves_other_hooks(runner: CliRunner, workspace: Path) -> None:
    settings_path = workspace / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    initial = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "other.py"}]},
                {"hooks": [{"type": "command", "command": "gptme-util hooks run"}]},
            ]
        }
    }
    settings_path.write_text(json.dumps(initial))
    runner.invoke(main, ["hooks", "uninstall", "--workspace", str(workspace)])
    settings = json.loads(settings_path.read_text())
    entries = settings["hooks"]["UserPromptSubmit"]
    assert len(entries) == 1
    assert entries[0]["hooks"][0]["command"] == "other.py"


def test_uninstall_no_settings_is_ok(runner: CliRunner, workspace: Path) -> None:
    result = runner.invoke(main, ["hooks", "uninstall", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# hooks status
# ---------------------------------------------------------------------------


def test_status_shows_not_installed(runner: CliRunner, workspace: Path) -> None:
    result = runner.invoke(main, ["hooks", "status", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "UserPromptSubmit" in result.output


def test_status_shows_installed(runner: CliRunner, workspace: Path) -> None:
    runner.invoke(main, ["hooks", "install", "--workspace", str(workspace)])
    result = runner.invoke(main, ["hooks", "status", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "✅" in result.output


# ---------------------------------------------------------------------------
# hooks run
# ---------------------------------------------------------------------------


def _make_event(
    event_type: str = "UserPromptSubmit",
    session_id: str | None = "test-session",
    prompt: str = "",
    tool_name: str = "",
    tool_input: dict | None = None,
    transcript: list | None = None,
) -> str:
    data: dict = {
        "hook_event_name": event_type,
    }
    if session_id is not None:
        data["session_id"] = session_id
    if prompt:
        data["prompt"] = prompt
    if tool_name:
        data["tool_name"] = tool_name
    if tool_input is not None:
        data["tool_input"] = tool_input
    if transcript is not None:
        data["transcript"] = transcript
    return json.dumps(data)


def test_run_empty_stdin_returns_continue(runner: CliRunner) -> None:
    result = runner.invoke(main, ["hooks", "run"], input="")
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out.get("continue") is True


def test_run_invalid_json_returns_continue(runner: CliRunner) -> None:
    result = runner.invoke(main, ["hooks", "run"], input="not-json")
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out.get("continue") is True


def test_run_no_matching_lesson_returns_continue(
    runner: CliRunner, tmp_path: Path
) -> None:
    """An event with no keyword matches returns continue without additionalContext."""
    (tmp_path / "gptme.toml").write_text(f'[lessons]\ndirs = ["{tmp_path}/lessons"]\n')
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    # Write a lesson that won't match the prompt
    (lessons_dir / "test.md").write_text(
        textwrap.dedent("""\
            ---
            match:
              keywords:
                - very specific phrase xyz
            status: active
            ---
            # Test Lesson
            body text
        """)
    )
    event = _make_event(prompt="unrelated query about something else")
    result = runner.invoke(
        main,
        ["hooks", "run", "--workspace", str(tmp_path)],
        input=event,
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    # Should return continue=True; additionalContext may or may not be set
    assert out.get("continue") is True
    assert "additionalContext" not in out


def test_run_matching_lesson_returns_context(runner: CliRunner, tmp_path: Path) -> None:
    """An event matching a lesson keyword returns additionalContext."""
    (tmp_path / "gptme.toml").write_text(f'[lessons]\ndirs = ["{tmp_path}/lessons"]\n')
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "my_lesson.md").write_text(
        textwrap.dedent("""\
            ---
            match:
              keywords:
                - frobnicate the widget
            status: active
            ---
            # Frob Lesson
            Always frobnicate widgets before use.
        """)
    )
    event = _make_event(
        session_id="unique-session-abc",
        prompt="I need to frobnicate the widget in my code",
    )
    result = runner.invoke(
        main,
        ["hooks", "run", "--workspace", str(tmp_path)],
        input=event,
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert "additionalContext" in out
    assert "Frob Lesson" in out["additionalContext"]
    assert out.get("continue") is True


def test_run_archived_lesson_skipped(runner: CliRunner, tmp_path: Path) -> None:
    """Lessons with status: archived are not injected."""
    (tmp_path / "gptme.toml").write_text(f'[lessons]\ndirs = ["{tmp_path}/lessons"]\n')
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "archived.md").write_text(
        textwrap.dedent("""\
            ---
            match:
              keywords:
                - frobnicate the widget
            status: archived
            ---
            # Archived Lesson
            This is archived.
        """)
    )
    event = _make_event(
        session_id="session-archived",
        prompt="I need to frobnicate the widget",
    )
    result = runner.invoke(
        main,
        ["hooks", "run", "--workspace", str(tmp_path)],
        input=event,
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    # Archived lesson should not appear
    assert "additionalContext" not in out or "Archived Lesson" not in out.get(
        "additionalContext", ""
    )


def test_run_unknown_event_returns_continue(runner: CliRunner) -> None:
    event = _make_event(event_type="SomeOtherEvent")
    result = runner.invoke(main, ["hooks", "run"], input=event, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out.get("continue") is True


def test_run_stale_workspace_returns_valid_json(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Stale GPTME_WORKSPACE must not cause Click to exit without JSON output."""
    stale_path = tmp_path / "deleted-workspace"
    # Intentionally do NOT create this directory — it's stale.
    event = _make_event(prompt="something interesting")
    result = runner.invoke(
        main,
        ["hooks", "run", "--workspace", str(stale_path)],
        input=event,
        catch_exceptions=False,
    )
    # Must exit 0 and output valid JSON (the hook contract).
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out.get("continue") is True


def test_run_missing_session_id_uses_unique_fallback(
    runner: CliRunner, tmp_path: Path
) -> None:
    """When CC omits session_id, each invocation gets an isolated dedup state.

    Two invocations without session_id must not share the same anonymous session
    (which would cause the second invocation to silently skip lessons already
    seen in the first).
    """
    (tmp_path / "gptme.toml").write_text(f'[lessons]\ndirs = ["{tmp_path}/lessons"]\n')
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "frob.md").write_text(
        textwrap.dedent("""\
            ---
            match:
              keywords:
                - frobnicate the widget
            status: active
            ---
            # Frob Lesson
            Always frobnicate widgets before use.
        """)
    )
    # Two events with no session_id — each should get its own anon ID and thus
    # its own fresh dedup state, so both invocations return the matching lesson.
    event = _make_event(session_id=None, prompt="I need to frobnicate the widget")
    for _ in range(2):
        result = runner.invoke(
            main,
            ["hooks", "run", "--workspace", str(tmp_path)],
            input=event,
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        # Each anonymous invocation should see the lesson (no cross-session dedup)
        assert "additionalContext" in out, "lesson should be injected for anon session"
        assert "Frob Lesson" in out["additionalContext"]


# ---------------------------------------------------------------------------
# _extract_pretooluse_text helper
# ---------------------------------------------------------------------------


def test_extract_pretooluse_text_tool_name() -> None:
    hook_input = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    text = _extract_pretooluse_text(hook_input)
    assert "Bash" in text
    assert "git status" in text


def test_extract_pretooluse_text_transcript() -> None:
    hook_input = {
        "tool_name": "Bash",
        "tool_input": {},
        "transcript": [
            {"role": "assistant", "content": "I'll fix the merge conflict"},
            {"role": "tool", "content": "CONFLICT (content): Merge conflict"},
        ],
    }
    text = _extract_pretooluse_text(hook_input)
    assert "merge conflict" in text.lower()
