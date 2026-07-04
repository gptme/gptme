"""Tests for external agent session search (Cursor, Codex)."""

import json

from click.testing import CliRunner

from gptme.cli.cmd_chats import chats
from gptme.tools.chats import (
    _discover_codex_sessions,
    _discover_cursor_sessions,
    _search_codex_session,
    _search_cursor_session,
    search_external_chats,
)

# ---------------------------------------------------------------------------
# Cursor — discovery
# ---------------------------------------------------------------------------


def test_discover_cursor_sessions_missing_dir(tmp_path):
    """Returns empty list when directory does not exist."""
    assert _discover_cursor_sessions(tmp_path / "nonexistent") == []


def test_discover_cursor_sessions(tmp_path):
    """Discovers conversation.json files in cursor_dir."""
    session_dir = tmp_path / "abc-123"
    session_dir.mkdir()
    conv_file = session_dir / "conversation.json"
    conv_file.write_text(json.dumps({"id": "abc", "messages": []}))
    assert _discover_cursor_sessions(tmp_path) == [conv_file]


def test_discover_cursor_sessions_discovers_alternate_format(tmp_path):
    """Discovers cursor-chat-history.json at parent level alongside conversation.json."""
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    session_dir = conv_dir / "abc-123"
    session_dir.mkdir()
    conv_file = session_dir / "conversation.json"
    conv_file.write_text(json.dumps({"messages": []}))
    alt_file = tmp_path / "cursor-chat-history.json"
    alt_file.write_text(json.dumps({"allConversations": []}))
    results = _discover_cursor_sessions(conv_dir)
    assert conv_file in results
    assert alt_file in results
    assert len(results) == 2


def test_discover_cursor_sessions_alternate_only(tmp_path):
    """Returns only cursor-chat-history.json when conversation dir is empty."""
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    alt_file = tmp_path / "cursor-chat-history.json"
    alt_file.write_text(json.dumps({"allConversations": []}))
    results = _discover_cursor_sessions(conv_dir)
    assert results == [alt_file]


def test_discover_cursor_sessions_no_conversations_dir(tmp_path):
    """Returns cursor-chat-history.json even when conversations/ dir does not exist."""
    conv_dir = tmp_path / "conversations"  # intentionally NOT created
    alt_file = tmp_path / "cursor-chat-history.json"
    alt_file.write_text(json.dumps({"allConversations": []}))
    results = _discover_cursor_sessions(conv_dir)
    assert results == [alt_file]


def test_discover_cursor_sessions_neither_exists(tmp_path):
    """Returns empty list when neither conversations/ nor alt file exists."""
    conv_dir = tmp_path / "conversations"  # intentionally NOT created
    results = _discover_cursor_sessions(conv_dir)
    assert results == []


def test_discover_cursor_sessions_multiple(tmp_path):
    """Returns all conversation.json files sorted alphabetically."""
    for uid in ("bbb-222", "aaa-111"):
        d = tmp_path / uid
        d.mkdir()
        (d / "conversation.json").write_text("{}")
    results = _discover_cursor_sessions(tmp_path)
    assert len(results) == 2
    assert results[0].parent.name == "aaa-111"


# ---------------------------------------------------------------------------
# Cursor — search (standard format)
# ---------------------------------------------------------------------------


def test_search_cursor_session_match(tmp_path):
    """Finds matching messages in standard Cursor format."""
    d = tmp_path / "abc-123"
    d.mkdir()
    f = d / "conversation.json"
    f.write_text(
        json.dumps(
            {
                "id": "abc",
                "title": "Debug CORS",
                "messages": [
                    {"id": "1", "role": "user", "content": "I have a CORS error"},
                    {
                        "id": "2",
                        "role": "assistant",
                        "content": "Let me fix CORS for you",
                    },
                ],
            }
        )
    )
    results = _search_cursor_session(f, "CORS")
    assert len(results) == 2
    assert results[0]["role"] == "user"
    assert results[1]["role"] == "assistant"
    assert results[0]["session_title"] == "Debug CORS"


def test_search_cursor_session_no_match(tmp_path):
    """Returns empty list when no messages match."""
    d = tmp_path / "abc-123"
    d.mkdir()
    f = d / "conversation.json"
    f.write_text(json.dumps({"messages": [{"role": "user", "content": "Hello world"}]}))
    assert _search_cursor_session(f, "CORS") == []


def test_search_cursor_session_case_insensitive(tmp_path):
    """Query matching is case-insensitive."""
    d = tmp_path / "abc-123"
    d.mkdir()
    f = d / "conversation.json"
    f.write_text(
        json.dumps({"messages": [{"role": "user", "content": "cors problem"}]})
    )
    assert len(_search_cursor_session(f, "CORS")) == 1


def test_search_cursor_session_invalid_json(tmp_path):
    """Handles invalid JSON gracefully and returns empty list."""
    d = tmp_path / "abc-123"
    d.mkdir()
    f = d / "conversation.json"
    f.write_text("not valid json")
    assert _search_cursor_session(f, "anything") == []


# ---------------------------------------------------------------------------
# Cursor — search (alternate workspace-storage format)
# ---------------------------------------------------------------------------


def test_search_cursor_session_alternate_format(tmp_path):
    """Handles cursor-chat-history.json / allConversations format."""
    d = tmp_path / "abc-123"
    d.mkdir()
    f = d / "cursor-chat-history.json"
    f.write_text(
        json.dumps(
            {
                "allConversations": [
                    {
                        "composerId": "comp-1",
                        "bubbles": [
                            {"type": "user", "text": "Fix CORS headers please"},
                            {
                                "type": "ai",
                                "rawResponse": {
                                    "text": "Add CORS headers to the response"
                                },
                            },
                        ],
                    }
                ]
            }
        )
    )
    results = _search_cursor_session(f, "CORS")
    assert len(results) == 2
    assert results[0]["role"] == "user"
    assert results[1]["role"] == "assistant"
    assert results[0]["session_title"] == "comp-1"


# ---------------------------------------------------------------------------
# Codex — discovery
# ---------------------------------------------------------------------------


def test_discover_codex_sessions_missing_dir(tmp_path):
    """Returns empty list when directory does not exist."""
    assert _discover_codex_sessions(tmp_path / "nonexistent") == []


def test_discover_codex_sessions(tmp_path):
    """Discovers rollout-*.jsonl files under date-partitioned subdirs."""
    session_dir = tmp_path / "2026" / "03" / "27"
    session_dir.mkdir(parents=True)
    f = session_dir / "rollout-2026-03-27T13-20-54-abc.jsonl"
    f.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": []},
            }
        )
    )
    assert _discover_codex_sessions(tmp_path) == [f]


def test_discover_codex_sessions_multiple(tmp_path):
    """Returns all rollout-*.jsonl files sorted, across date subdirs."""
    for date, name in (
        ("2026/03/27", "rollout-2026-03-27T00-00-00-b.jsonl"),
        ("2026/03/26", "rollout-2026-03-26T00-00-00-a.jsonl"),
    ):
        d = tmp_path / date
        d.mkdir(parents=True)
        (d / name).write_text("{}")
    results = _discover_codex_sessions(tmp_path)
    assert len(results) == 2
    assert results[0].name == "rollout-2026-03-26T00-00-00-a.jsonl"


def test_discover_codex_sessions_ignores_non_rollout_files(tmp_path):
    """Ignores .jsonl files that don't match the rollout-*.jsonl pattern."""
    session_dir = tmp_path / "2026" / "03" / "27"
    session_dir.mkdir(parents=True)
    (session_dir / "other.jsonl").write_text("{}")
    assert _discover_codex_sessions(tmp_path) == []


# ---------------------------------------------------------------------------
# Codex — search
# ---------------------------------------------------------------------------


def _codex_message(role: str, text: str, part_type: str = "input_text") -> str:
    """Build a real-shape Codex ``response_item``/``message`` JSONL line."""
    return json.dumps(
        {
            "timestamp": "2026-07-01T10:00:00.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": role,
                "content": [{"type": part_type, "text": text}],
            },
        }
    )


def test_search_codex_session_match(tmp_path):
    """Finds matching message records in real Codex response_item format."""
    f = tmp_path / "sess-1.jsonl"
    f.write_text(
        "\n".join(
            [
                _codex_message("user", "Create a CORS middleware"),
                _codex_message(
                    "assistant", "Here is a CORS middleware example", "output_text"
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": "abc"},
                    }
                ),
            ]
        )
    )
    results = _search_codex_session(f, "CORS")
    assert len(results) == 2
    assert results[0]["role"] == "user"
    assert results[1]["role"] == "assistant"
    assert results[0]["session_title"] == "sess-1"


def test_search_codex_session_no_match(tmp_path):
    """Returns empty list when no messages match."""
    f = tmp_path / "sess-1.jsonl"
    f.write_text(_codex_message("user", "Hello world"))
    assert _search_codex_session(f, "CORS") == []


def test_search_codex_session_invalid_json_line(tmp_path):
    """Handles malformed JSON lines gracefully and continues."""
    f = tmp_path / "sess-1.jsonl"
    f.write_text("not valid json\n" + _codex_message("user", "CORS fix needed"))
    results = _search_codex_session(f, "CORS")
    assert len(results) == 1


def test_search_codex_session_skips_non_message_records(tmp_path):
    """Non-message record types (session_meta, event_msg, tool calls) are skipped."""
    f = tmp_path / "sess-1.jsonl"
    f.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "abc", "cwd": "CORS something"},
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "bash",
                            "arguments": "CORS something",
                        },
                    }
                ),
            ]
        )
    )
    assert _search_codex_session(f, "CORS") == []


# ---------------------------------------------------------------------------
# search_external_chats — integration
# ---------------------------------------------------------------------------


def test_search_external_chats_no_results(tmp_path, capsys):
    """Prints nothing when no external sessions match."""
    search_external_chats(
        "CORS",
        cursor_dir=tmp_path / "cursor",
        codex_dir=tmp_path / "codex",
    )
    captured = capsys.readouterr()
    assert captured.out == ""


def test_search_external_chats_cursor_match(tmp_path, capsys):
    """Prints Cursor hits with [Cursor] label."""
    cursor_dir = tmp_path / "cursor"
    session_dir = cursor_dir / "abc-123"
    session_dir.mkdir(parents=True)
    (session_dir / "conversation.json").write_text(
        json.dumps(
            {
                "title": "My CORS session",
                "messages": [{"role": "user", "content": "CORS error help"}],
            }
        )
    )
    search_external_chats(
        "CORS",
        cursor_dir=cursor_dir,
        codex_dir=tmp_path / "codex",
    )
    out = capsys.readouterr().out
    assert "[Cursor]" in out
    assert "My CORS session" in out


def test_search_external_chats_early_exit(tmp_path, capsys):
    """Stops scanning after max_results sessions are collected."""
    cursor_dir = tmp_path / "cursor"
    for i in range(10):
        session_dir = cursor_dir / f"sess-{i:03d}"
        session_dir.mkdir(parents=True)
        (session_dir / "conversation.json").write_text(
            json.dumps(
                {
                    "title": f"Session {i}",
                    "messages": [{"role": "user", "content": f"CORS issue {i}"}],
                }
            )
        )
    search_external_chats(
        "CORS",
        max_results=3,
        cursor_dir=cursor_dir,
        codex_dir=tmp_path / "codex",
    )
    out = capsys.readouterr().out
    session_count = out.count("[Cursor]")
    assert session_count <= 3, (
        f"Expected ≤3 Cursor sessions in output, got {session_count}"
    )


# ---------------------------------------------------------------------------
# CLI integration — --all-agents with --json
# ---------------------------------------------------------------------------


def test_cli_chats_search_json_all_agents_warning(tmp_path, monkeypatch):
    """--all-agents with --json prints a warning and still succeeds."""
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(chats, ["search", "CORS", "--json", "--all-agents"])
    assert result.exit_code == 0
    assert "Warning: --all-agents is not supported with --json" in result.output


def test_search_external_chats_codex_match(tmp_path, capsys):
    """Prints Codex hits with [Codex] label."""
    codex_dir = tmp_path / "codex" / "2026" / "03" / "27"
    codex_dir.mkdir(parents=True)
    (codex_dir / "rollout-2026-03-27T00-00-00-sess-xyz.jsonl").write_text(
        _codex_message("user", "CORS in codex")
    )
    search_external_chats(
        "CORS",
        cursor_dir=tmp_path / "cursor",
        codex_dir=tmp_path / "codex",
    )
    out = capsys.readouterr().out
    assert "[Codex]" in out
    assert "sess-xyz" in out


# ---------------------------------------------------------------------------
# CLI integration — --all-agents flag
# ---------------------------------------------------------------------------


def test_cli_chats_search_all_agents_no_external(tmp_path, monkeypatch, capsys):
    """--all-agents flag runs without error when no external sessions exist."""
    # Redirect home so no real ~/.cursor / ~/.codex are read
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(chats, ["search", "CORS", "--all-agents"])
    # The gptme search part may print "No results found" — that's fine.
    # We only care that the command doesn't crash.
    assert result.exit_code == 0 or "No results" in (result.output or "")
