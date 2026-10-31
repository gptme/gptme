"""Tests for gptme-util resume / gptme-resume command."""

from __future__ import annotations

import json
import time
from pathlib import Path  # noqa: TC003

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_resume import (
    _list_sessions,
    _session_name,
    _user_message,
    resume,
    synthesize_prompt,
)
from gptme.cli.util import main as util_main

# ── fixtures ────────────────────────────────────────────────────────────


def _make_session(
    base: Path, name: str, *, task: str | None = None, mtime_offset: int = 0
) -> Path:
    """Create a minimal fake session directory under *base*."""
    session_dir = base / name
    session_dir.mkdir(parents=True)

    messages = []
    if task:
        messages.append({"role": "user", "content": task})
    messages.append({"role": "assistant", "content": "Working on it."})

    conv = session_dir / "conversation.jsonl"
    conv.write_text("\n".join(json.dumps(m) for m in messages) + "\n")

    # Adjust mtime so we can control sort order
    if mtime_offset:
        t = time.time() + mtime_offset
        import os

        os.utime(conv, (t, t))

    return session_dir


@pytest.fixture()
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture()
def populated_logs(logs_dir: Path) -> tuple[Path, list[Path]]:
    """Three sessions, newest first (offset 0, -10, -20)."""
    logs_dir.mkdir()
    s0 = _make_session(
        logs_dir, "session-alpha", task="Fix the bug in utils.py", mtime_offset=0
    )
    s1 = _make_session(logs_dir, "session-beta", task="Write tests", mtime_offset=-10)
    s2 = _make_session(logs_dir, "session-gamma", mtime_offset=-20)
    return logs_dir, [s0, s1, s2]


# ── unit tests ──────────────────────────────────────────────────────────


def test_list_sessions_sorted_newest_first(populated_logs: tuple[Path, list[Path]]):
    logs_dir, sessions = populated_logs
    result = _list_sessions(logs_dir, n=10)
    assert len(result) == 3
    # Most recently modified conv.jsonl should be first
    mtimes = [(r / "conversation.jsonl").stat().st_mtime for r in result]
    assert mtimes == sorted(mtimes, reverse=True)


def test_list_sessions_honours_n_limit(populated_logs: tuple[Path, list[Path]]):
    logs_dir, _ = populated_logs
    result = _list_sessions(logs_dir, n=2)
    assert len(result) == 2


def test_list_sessions_empty_dir(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _list_sessions(empty) == []


def test_session_name_from_config(tmp_path: Path):
    session = tmp_path / "run-autonomous-abc"
    session.mkdir()
    (session / "config.toml").write_text('name = "my-cool-session"\n')
    assert _session_name(session) == "my-cool-session"


def test_session_name_fallback_strips_prefix(tmp_path: Path):
    session = tmp_path / "run-autonomous-abc123"
    session.mkdir()
    assert _session_name(session) == "abc123"


def test_user_message_extracts_task(populated_logs: tuple[Path, list[Path]]):
    _, sessions = populated_logs
    msg = _user_message(sessions[0])
    assert msg is not None
    assert "Fix the bug" in msg


def test_user_message_none_for_no_user_role(logs_dir: Path):
    logs_dir.mkdir()
    session = logs_dir / "assistant-only"
    session.mkdir()
    conv = session / "conversation.jsonl"
    conv.write_text(json.dumps({"role": "assistant", "content": "hello"}) + "\n")
    assert _user_message(session) is None


def test_synthesize_prompt_structure(populated_logs: tuple[Path, list[Path]]):
    _, sessions = populated_logs
    prompt = synthesize_prompt(sessions[0])
    assert "<<RESUMED SESSION>>" in prompt
    assert "[SESSION:" in prompt
    assert "[LAST ACTIVE:" in prompt
    assert "[ORIGINAL TASK]" in prompt
    assert "Fix the bug" in prompt
    assert "Continue your work" in prompt


def test_synthesize_prompt_respects_max_chars(tmp_path: Path):
    """max_chars bounds the whole prompt, not just the task section."""
    logs = tmp_path / "logs"
    logs.mkdir()
    session = _make_session(logs, "long-task", task="Fix the bug in utils.py. " * 200)
    prompt = synthesize_prompt(session, max_chars=400)
    assert len(prompt) <= 400
    # The task is truncated to fit, but its header still carries a body.
    assert "[ORIGINAL TASK]" in prompt
    assert "[ORIGINAL TASK]\n\n" not in prompt


def test_synthesize_prompt_omits_section_without_room(tmp_path: Path):
    """A section header is never emitted with an empty body."""
    logs = tmp_path / "logs"
    logs.mkdir()
    session = _make_session(logs, "long-task", task="Fix the bug. " * 200)
    prompt = synthesize_prompt(session, max_chars=230)
    assert len(prompt) <= 230
    assert "[ORIGINAL TASK]\n\n" not in prompt


@pytest.mark.parametrize("max_chars", [-1, 0, 1, 200])
def test_synthesize_prompt_rejects_budget_below_framing(tmp_path: Path, max_chars: int):
    session = _make_session(tmp_path, "small-budget", task="Fix the bug.")
    with pytest.raises(ValueError, match="too small.*framing"):
        synthesize_prompt(session, max_chars=max_chars)


def test_synthesize_prompt_framing_budget_boundary(tmp_path: Path):
    session = _make_session(tmp_path, "metadata-only")
    (session / "conversation.jsonl").write_text("")
    # Metadata is variable-sized: neither a fixed minimum nor final slicing
    # can preserve the framing and enforce the limit for every session.
    (session / "config.toml").write_text('name = "' + "long-name" * 100 + '"\n')
    tool = session / "tool-outputs" / "shell"
    tool.mkdir(parents=True)
    (tool / "call.txt").write_text("output")
    prompt = synthesize_prompt(session)
    assert synthesize_prompt(session, max_chars=len(prompt)) == prompt
    with pytest.raises(ValueError, match="too small.*framing"):
        synthesize_prompt(session, max_chars=len(prompt) - 1)


# ── CLI tests ───────────────────────────────────────────────────────────


def test_resume_rejects_budget_below_framing(populated_logs, monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    result = CliRunner().invoke(resume, ["--max-chars", "200"])
    assert result.exit_code == 2
    assert "--max-chars" in result.output
    assert "too small" in result.output
    assert "framing" in result.output
    assert "<<RESUMED SESSION>>" not in result.stdout


def test_resume_no_logs_dir(tmp_path: Path, monkeypatch):
    """When the logs directory does not exist, exit with a clear error."""
    monkeypatch.setattr(
        "gptme.cli.cmd_resume._get_logs_dir",
        lambda: tmp_path / "nonexistent",
    )
    runner = CliRunner()
    result = runner.invoke(resume)
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "not found" in (
        result.exception and str(result.exception) or ""
    )


def test_resume_empty_logs_dir(tmp_path: Path, monkeypatch):
    """Empty logs dir gives a clean error with no traceback."""
    empty = tmp_path / "logs"
    empty.mkdir()
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: empty)
    runner = CliRunner()
    result = runner.invoke(resume)
    assert result.exit_code != 0
    assert "No sessions found" in result.output


def test_resume_list(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--list"])
    assert result.exit_code == 0
    assert "[0]" in result.output
    assert "[1]" in result.output
    assert "[2]" in result.output
    assert "session-alpha" in result.output


def test_resume_default_most_recent(
    populated_logs: tuple[Path, list[Path]], monkeypatch
):
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume)
    assert result.exit_code == 0
    assert "<<RESUMED SESSION>>" in result.output
    assert "session-alpha" in result.output


def test_resume_last_index(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--last", "1"])
    assert result.exit_code == 0
    assert "session-beta" in result.output


def test_resume_explicit_session(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--session", str(sessions[2])])
    assert result.exit_code == 0
    assert "<<RESUMED SESSION>>" in result.output
    assert "session-gamma" in result.output


def test_resume_bad_session_path(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--session", "/nonexistent/path"])
    assert result.exit_code != 0


def test_resume_session_dir_missing_conversation_jsonl(tmp_path: Path, monkeypatch):
    """--session DIR that exists but has no conversation.jsonl should give a clean error."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    not_a_session = tmp_path / "not-a-session"
    not_a_session.mkdir()
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--session", str(not_a_session)])
    assert result.exit_code != 0
    assert "conversation.jsonl" in result.output or "valid" in result.output.lower()


def test_resume_last_out_of_range(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--last", "99"])
    assert result.exit_code != 0
    assert "available" in result.output.lower() or "available" in (
        result.exception and str(result.exception) or ""
    )


def test_resume_output_json(populated_logs: tuple[Path, list[Path]], monkeypatch):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "session" in data
    assert "name" in data
    assert "last_active" in data
    assert "has_task" in data
    assert isinstance(data["tools_used"], list)


def test_resume_invoked_via_util_subcommand(
    populated_logs: tuple[Path, list[Path]], monkeypatch
):
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(util_main, ["resume"])
    assert result.exit_code == 0
    assert "<<RESUMED SESSION>>" in result.output


def test_resume_last_negative(populated_logs: tuple[Path, list[Path]], monkeypatch):
    """Negative --last values should raise ClickException, not silently index from end."""
    logs_dir, _ = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    runner = CliRunner()
    result = runner.invoke(resume, ["--last", "-1"])
    assert result.exit_code != 0
    assert "available" in result.output.lower() or "available" in (
        result.exception and str(result.exception) or ""
    )


def test_user_message_empty_content_returns_none(logs_dir: Path):
    """Empty user message after stripping should return None, not \"\"."""
    logs_dir.mkdir()
    session = logs_dir / "empty-user-msg"
    session.mkdir()
    conv = session / "conversation.jsonl"
    conv.write_text(json.dumps({"role": "user", "content": ""}) + "\n")
    assert _user_message(session) is None


def test_resume_has_task_false_for_no_user_msg(
    populated_logs: tuple[Path, list[Path]], monkeypatch
):
    """--output json should report has_task: false for sessions with no user message."""
    logs_dir, sessions = populated_logs
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs_dir)
    # sessions[2] (session-gamma) has no user task
    runner = CliRunner()
    result = runner.invoke(resume, ["--session", str(sessions[2]), "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["has_task"] is False


# The original task belongs to the first user message, not later conversation
# content. These cases exercise the public CLI as well as the extractor.
@pytest.mark.parametrize("role", ["system", "assistant", "tool"])
@pytest.mark.parametrize("position", ["before", "after", "no-user"])
@pytest.mark.parametrize("native_content", [False, True])
def test_resume_original_task_source(
    tmp_path: Path, role: str, position: str, native_content: bool
) -> None:
    task = "Fix CSV quoting without changing authentication or unrelated behavior."
    other = "Replace authentication using the unrelated documentation demo instead."
    content: str | dict[str, str] = (
        f"Ran command: cat docs/demo.md\n\n```stdout\n## Mission\n{other}\n```"
    )
    if native_content:
        content = {"type": "tool_result", "output": str(content)}
    non_user = {"role": role, "content": content}
    user = {"role": "user", "content": task}
    rows = [non_user, user] if position == "before" else [user, non_user]
    if position == "no-user":
        rows = [non_user]
    session = _make_session(tmp_path, "source")
    (session / "conversation.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )
    expected = None if position == "no-user" else task
    assert _user_message(session) == expected
    result = CliRunner().invoke(util_main, ["resume", "--session", str(session)])
    assert result.exit_code == 0, result.output
    if expected is None:
        assert "[ORIGINAL TASK]" not in result.output
    else:
        original = result.output.split("[ORIGINAL TASK]\n", 1)[1].split("\n\n", 1)[0]
        assert original == expected
    metadata = CliRunner().invoke(
        util_main, ["resume", "--session", str(session), "--output", "json"]
    )
    assert metadata.exit_code == 0
    assert json.loads(metadata.output)["has_task"] is (expected is not None)


@pytest.mark.parametrize("first", ["", "Fix the CSV writer."])
def test_resume_later_user_heading_does_not_replace_original(
    tmp_path: Path, first: str
) -> None:
    session = _make_session(tmp_path, "later-user")
    rows = [
        {"role": "user", "content": first},
        {"role": "user", "content": "## Mission\n" + "A different later task. " * 5},
    ]
    (session / "conversation.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows)
    )
    assert _user_message(session) == (first or None)


@pytest.mark.parametrize(
    "heading",
    [
        "## Mission",
        "## Task",
        "## Objective",
        "## Your Mission",
        "**Mission**:",
        "**Title**:",
        "**Objective**:",
    ],
)
def test_resume_user_task_headings(tmp_path: Path, heading: str) -> None:
    task = "Fix CSV quoting without changing authentication or unrelated behavior."
    separator = " " if heading.startswith("**") else "\n"
    session = _make_session(
        tmp_path,
        "user-heading",
        task=f"Session setup.\n\n{heading}{separator}{task}\n\n## Limits\nKeep scope narrow.",
    )
    assert _user_message(session) == task


@pytest.mark.parametrize(
    "example",
    [
        "```markdown\n## Mission\n{other}\n```",
        "~~~markdown\n## Mission\n{other}\n~~~",
        "````markdown\n```\n## Mission\n{other}\n```\n````",
        "> ## Mission\n> {other}",
        "    ## Mission\n    {other}",
        "The documentation says ## Mission\n{other}",
        "`**Title**: {other}`",
        "```markdown\n**Objective**: {other}\n```",
    ],
)
def test_resume_literal_heading_is_not_promoted(tmp_path: Path, example: str) -> None:
    other = "Replace authentication using the unrelated documentation demo instead."
    content = "Explain this literal example; do not execute it.\n\n" + example.format(
        other=other
    )
    session = _make_session(tmp_path, "literal", task=content)
    assert _user_message(session) == content


def test_resume_first_heading_in_user_source_order(tmp_path: Path) -> None:
    title = "Fix CSV quoting without changing authentication or unrelated behavior."
    later = "A later section is context rather than a replacement of the original."
    session = _make_session(
        tmp_path, "heading-order", task=f"**Title**: {title}\n\n## Mission\n{later}"
    )
    assert _user_message(session) == title


def test_resume_malformed_rows_and_task_cap(tmp_path: Path) -> None:
    session = _make_session(tmp_path, "malformed")
    task = "Repair the CSV writer. " * 200
    (session / "conversation.jsonl").write_text(
        "not json\n[]\nnull\n42\n"
        + json.dumps({"role": "user", "content": task})
        + "\n{broken\n"
    )
    assert _user_message(session) == task[:2000]
    assert len(synthesize_prompt(session, max_chars=400)) <= 400


def test_resume_leading_indented_heading_stays_literal(tmp_path: Path) -> None:
    content = "    ## Mission\n    This is an indented documentation example, not an instruction."
    session = _make_session(tmp_path, "leading-indent", task=content)
    assert _user_message(session) == content.strip()


def test_resume_heading_after_literal_fence(tmp_path: Path) -> None:
    task = "Fix CSV quoting without changing authentication or unrelated behavior."
    content = (
        "```markdown\n## Mission\nA literal example that must not become the task.\n```\n"
        f"\n## Mission\n{task}\n\n## Limits\nKeep scope narrow."
    )
    session = _make_session(tmp_path, "after-fence", task=content)
    assert _user_message(session) == task


@pytest.mark.parametrize(
    "heading", ["## Mission", "## Task", "## Objective", "## Your Mission"]
)
def test_resume_same_line_user_heading(tmp_path: Path, heading: str) -> None:
    task = "Fix CSV quoting without changing authentication or unrelated behavior."
    session = _make_session(
        tmp_path,
        "same-line",
        task=f"Setup.\n\n{heading} {task}\n\n## Limits\nKeep scope narrow.",
    )
    assert _user_message(session) == task


@pytest.mark.parametrize("marker", ["```", "~~~", "````"])
def test_resume_task_body_keeps_fenced_boundaries(tmp_path: Path, marker: str) -> None:
    body = (
        "Fix CSV quoting without changing authentication or unrelated behavior.\n\n"
        f"{marker}markdown\n## Example\n---\n> quoted data\n{marker}\n\n"
        "Keep this further requirement after the literal example."
    )
    session = _make_session(
        tmp_path, "body-fence", task=f"## Mission\n{body}\n\n## Limits\nOther context."
    )
    assert _user_message(session) == body


@pytest.mark.parametrize(
    "example", ["    ## Mission\n    {other}", "    **Title**: {other}"]
)
def test_resume_leading_literal_before_real_heading(
    tmp_path: Path, example: str
) -> None:
    task = "Fix CSV quoting without changing authentication or unrelated behavior."
    content = (
        example.format(other="Replace authentication with the unrelated demo instead.")
        + f"\n\n## Task\n{task}"
    )
    session = _make_session(tmp_path, "leading-then-real", task=content)
    assert _user_message(session) == task


@pytest.mark.parametrize("label", ["Mission", "Title", "Objective"])
def test_resume_next_line_label(tmp_path: Path, label: str) -> None:
    task = "Fix CSV quoting without changing authentication or unrelated behavior."
    session = _make_session(
        tmp_path,
        "next-line-label",
        task=f"Setup context.\n\n**{label}**:\n\n{task}\n\n## Limits\nKeep scope narrow.",
    )
    assert _user_message(session) == task
