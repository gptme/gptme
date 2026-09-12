"""Triggered memory delivery uses explicit lesson keywords, not relevance scores."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.util import main as util_main
from gptme.memory import MemoryEntry, MemoryRoot, MemoryStore, parse_entry


def write_entry(root: Path, name: str, **kwargs) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(MemoryEntry(name=name, **kwargs).to_markdown())
    return path


def test_match_cli_triggers_keyworded_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(
        tmp_path, "release-policy", keywords=["stable release"], body="Verify the tag."
    )
    result = CliRunner().invoke(
        util_main, ["memory", "match", "Cut the STABLE RELEASE", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    hit = json.loads(result.output)["hits"][0]
    assert hit["name"] == "release-policy"
    assert hit["matched_by"] == ["keyword:stable release"]
    assert hit["body"] == "Verify the tag."


def test_match_lifecycle_shadowing_and_unselected_entries(tmp_path):
    from gptme.memory.match import match_memories

    near, far = tmp_path / "near", tmp_path / "far"
    write_entry(near, "retired", status="historical", keywords=["release"])
    write_entry(far, "retired", keywords=["release"])
    write_entry(near, "replaced", status="superseded", keywords=["release"])
    write_entry(near, "ambient-only", description="release", body="release")
    write_entry(near, "unselected", keywords=["release"])
    (near / ".memory-index.json").write_text(
        '{"version": 1, "budget": 100, "selected": []}'
    )
    (near / "MEMORY.md").write_text("Preserve this view byte for byte.\n")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    store = MemoryStore([MemoryRoot("project", near), MemoryRoot("user", far)])
    assert [h.entry.name for h in match_memories(store, "release")] == ["unselected"]
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_match_uses_lesson_wildcards_and_keyword_only_ranking(tmp_path):
    from gptme.memory.match import match_memories

    write_entry(tmp_path, "two", keywords=["time*", "retry"])
    write_entry(tmp_path, "one", keywords=["retry"])
    write_entry(tmp_path, "timeouts-retry", keywords=["unrelated"])
    write_entry(tmp_path, "disabled", keywords=["", "*", "  "])
    write_entry(tmp_path, "punctuation", keywords=["time*out"])
    store = MemoryStore([MemoryRoot("project", tmp_path)])
    hits = match_memories(store, "TIMEOUTS retry time-out")
    assert [(h.entry.name, h.score) for h in hits] == [
        ("two", 2),
        ("one", 1),
        ("punctuation", 1),
    ]
    assert len(match_memories(store, "timeouts retry", limit=1)) == 1
    assert match_memories(store, "  ") == []
    assert match_memories(store, "time-out")[0].entry.name == "two"
    assert all(h.entry.name != "punctuation" for h in match_memories(store, "time-out"))
    with pytest.raises(ValueError, match="positive"):
        match_memories(store, "retry", limit=0)


@pytest.mark.parametrize("pretool", [False, True])
def test_match_hook_payload_event_and_bounded_body(tmp_path, monkeypatch, pretool):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(tmp_path, "release-rule", keywords=["release"], body="x" * 100)
    event = "PreToolUse" if pretool else "UserPromptSubmit"
    payload: dict[str, object] = {"hook_event_name": event, "session_id": "release-id"}
    payload.update(
        {"tool_name": "Bash", "tool_input": {"command": "release"}}
        if pretool
        else {"prompt": "release"}
    )
    result = CliRunner().invoke(
        util_main,
        [
            "memory",
            "match",
            "--prompt",
            "-",
            "--format",
            "hook-json",
            "--body-chars",
            "12",
        ],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)["hookSpecificOutput"]
    assert output["hookEventName"] == event
    assert "release-rule" in output["additionalContext"]
    assert "x" * 13 not in output["additionalContext"]


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": "Stop", "prompt": "release"},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "release"},
        {"hook_event_name": "UserPromptSubmit", "prompt": {"text": "release"}},
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {},
            "cwd": "release",
        },
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Custom",
            "tool_input": {
                "cwd": "release",
                "nested": {"session_id": "release"},
                "items": [{"transcript_path": "release"}],
            },
        },
        ["release"],
    ],
)
def test_match_ignores_invalid_or_unrelated_hook_fields(tmp_path, monkeypatch, payload):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(tmp_path, "rule", keywords=["release"])
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--prompt", "-", "--format", "json"],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["hits"] == []


def test_match_preserves_unsupported_hook_event_name(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(tmp_path, "rule", keywords=["release"])
    payload = {"hook_event_name": "Stop", "prompt": "release"}
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--prompt", "-", "--format", "hook-json"],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)["hookSpecificOutput"]
    assert output == {"hookEventName": "Stop", "additionalContext": ""}


def test_match_plain_pretool_and_empty_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(tmp_path, "rule", keywords=["release"])
    runner = CliRunner()
    for query, expected in [("release", True), ("unrelated", False)]:
        result = runner.invoke(
            util_main, ["memory", "match", query, "--pre-tool", "--format", "hook-json"]
        )
        assert result.exit_code == 0, result.output
        output = json.loads(result.output)["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse"
        assert bool(output["additionalContext"]) == expected


def test_save_keywords_preserves_policy_and_omitted_keywords(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    (tmp_path / ".memory-index.json").write_text(
        '{"version": 1, "budget": 100, "selected": []}'
    )
    runner = CliRunner()
    result = runner.invoke(
        util_main,
        [
            "memory",
            "save",
            "rule",
            "Release policy",
            "--keyword",
            "stable release",
            "--keyword",
            "deploy*",
        ],
        input="Verify.\n",
    )
    assert result.exit_code == 0, result.output
    assert parse_entry(tmp_path / "rule.md").keywords == ["stable release", "deploy*"]
    index = (tmp_path / "MEMORY.md").read_bytes()
    result = runner.invoke(
        util_main, ["memory", "save", "rule", "Updated policy"], input="Updated.\n"
    )
    assert result.exit_code == 0, result.output
    assert parse_entry(tmp_path / "rule.md").keywords == ["stable release", "deploy*"]
    assert (tmp_path / "MEMORY.md").read_bytes() == index
