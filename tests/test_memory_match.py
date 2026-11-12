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


def test_match_pretool_includes_nested_custom_tool_arguments(tmp_path, monkeypatch):
    """Nested tool_input keys that reuse envelope names still participate.

    Outer payload session_id/cwd/transcript_path are ignored; the same names
    under tool_input are arguments of the pending tool call.
    """
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(tmp_path, "rule", keywords=["release"])
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "unrelated",
        "cwd": "unrelated",
        "transcript_path": "unrelated",
        "tool_input": {
            "cwd": "release",
            "nested": {"session_id": "release"},
            "items": [{"transcript_path": "release"}],
        },
    }
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--prompt", "-", "--format", "json"],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.output
    assert [hit["name"] for hit in json.loads(result.output)["hits"]] == ["rule"]


def test_match_hook_json_truncates_multibyte_bodies_on_code_points(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(tmp_path, "release-rule", keywords=["release"], body="é" * 20)
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "release"}
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
            "8",
        ],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "é" in ctx
    assert "\ufffd" not in ctx
    assert ctx.encode("utf-8")


def test_pretool_string_values_walk_is_iterative():
    """2,000-level trees exceed CPython 3.10's recursive json codec.

    The walker must not depend on json.dumps/loads, which RecursionError
    on the no-extras CI job (Python 3.10) before match code runs.
    """
    from gptme.cli.cmd_memory import _string_values

    nested: object = "release"
    for _ in range(2000):
        nested = [nested]
    assert _string_values({"args": nested}) == ["release"]


def test_match_pretool_handles_nested_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    write_entry(tmp_path, "rule", keywords=["release"])
    nested: object = "release"
    for _ in range(32):
        nested = [nested]
    payload = {"hook_event_name": "PreToolUse", "tool_input": {"args": nested}}
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--prompt", "-", "--format", "json"],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.output
    assert [hit["name"] for hit in json.loads(result.output)["hits"]] == ["rule"]


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


def _hook(prompt: str, session: str | None) -> str:
    payload: dict[str, object] = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
    }
    if session is not None:
        payload["session_id"] = session
    return json.dumps(payload)


def _match_names(stdin: str, *extra: str) -> list[str]:
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--prompt", "-", "--format", "json", *extra],
        input=stdin,
    )
    assert result.exit_code == 0, result.output
    return [h["name"] for h in json.loads(result.output)["hits"]]


def test_match_dedup_session_delivers_each_entry_once(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_entry(tmp_path / "mem", "release-policy", keywords=["stable release"])
    first = _hook("cut the stable release", "s1")
    assert _match_names(first, "--dedup-session") == ["release-policy"]
    assert _match_names(first, "--dedup-session") == []
    # A new session gets it again; without the flag nothing is filtered.
    assert _match_names(_hook("stable release", "s2"), "--dedup-session") == [
        "release-policy"
    ]
    assert _match_names(first) == ["release-policy"]
    # State is keyed by a hash, never by the raw session id.
    files = list((tmp_path / "state").rglob("*.json"))
    assert len(files) == 2
    assert all("s1" not in f.name and "s2" not in f.name for f in files)


def test_match_dedup_cap_does_not_mark_undelivered(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for name in ("alpha", "beta"):
        write_entry(tmp_path / "mem", name, keywords=["deploy"])
    hook = _hook("deploy now", "s1")
    assert _match_names(hook, "--dedup-session", "-k", "1") == ["alpha"]
    assert _match_names(hook, "--dedup-session", "-k", "1") == ["beta"]
    assert _match_names(hook, "--dedup-session", "-k", "1") == []


def test_match_dedup_without_session_or_with_bad_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_entry(tmp_path / "mem", "release-policy", keywords=["stable release"])
    no_session = _hook("stable release", None)
    assert _match_names(no_session, "--dedup-session") == ["release-policy"]
    assert _match_names(no_session, "--dedup-session") == ["release-policy"]
    assert not (tmp_path / "state").exists()

    from gptme.memory.match import _seen_file

    bad = _seen_file("s1", tmp_path / "state" / "gptme")
    bad.parent.mkdir(parents=True)
    bad.write_text("not json")
    assert _match_names(_hook("stable release", "s1"), "--dedup-session") == [
        "release-policy"
    ]
    assert len(json.loads(bad.read_text())["seen"]) == 1  # rewritten, not ignored


def test_match_dedup_explicit_session_id_for_plain_text(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_entry(tmp_path / "mem", "release-policy", keywords=["stable release"])
    args = ["--dedup-session", "--session-id", "manual"]
    assert _match_names("stable release", *args) == ["release-policy"]
    assert _match_names("stable release", *args) == []


def test_match_dedup_state_write_failure_still_delivers(tmp_path, monkeypatch):
    """An unwritable state dir or full disk loses dedup, never the memories."""
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    write_entry(tmp_path / "mem", "release-policy", keywords=["stable release"])
    hook = _hook("cut the stable release", "s1")

    # State dir path is occupied by a regular file: mkdir() fails.
    (tmp_path / "state").write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert _match_names(hook, "--dedup-session") == ["release-policy"]

    # Directory exists but creating the temp file fails (e.g. ENOSPC).
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state2"))

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("gptme.memory.match.tempfile.mkstemp", full_disk)
    assert _match_names(hook, "--dedup-session") == ["release-policy"]
    assert _match_names(hook, "--dedup-session") == ["release-policy"]


@pytest.mark.parametrize(
    "state",
    ['{"seen": null}', '{"seen": [1]}', '{"seen": "abc"}', "[]", "null", '"x"'],
)
def test_match_dedup_malformed_state_counts_as_empty(tmp_path, monkeypatch, state):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_entry(tmp_path / "mem", "release-policy", keywords=["stable release"])
    from gptme.memory.match import _seen_file

    bad = _seen_file("s1", tmp_path / "state" / "gptme")
    bad.parent.mkdir(parents=True)
    bad.write_text(state)
    hook = _hook("stable release", "s1")
    assert _match_names(hook, "--dedup-session") == ["release-policy"]
    # Replaced with valid state, so the next call dedups normally.
    assert json.loads(bad.read_text())["seen"] == [
        str(tmp_path / "mem" / "release-policy.md")
    ]
    assert _match_names(hook, "--dedup-session") == []


def test_match_dedup_filters_full_match_set_before_cap(tmp_path, monkeypatch):
    """Seen entries must not crowd out unseen ones, however many match."""
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for i in range(1001):
        write_entry(tmp_path / "mem", f"rule-{i:04d}", keywords=["deploy"])
    from gptme.memory import MemoryStore
    from gptme.memory.match import _seen_file, match_memories

    ordered = [
        str(m.entry.path)
        for m in match_memories(MemoryStore.from_workspace(), "deploy", limit=5000)
    ]
    assert len(ordered) == 1001
    seen = _seen_file("s1", tmp_path / "state" / "gptme")
    seen.parent.mkdir(parents=True)
    seen.write_text(json.dumps({"seen": ordered[:1000]}))
    hook = _hook("deploy now", "s1")
    assert _match_names(hook, "--dedup-session", "-k", "1") == [
        Path(ordered[1000]).stem
    ]


def test_match_dedup_all_seen_calls_keep_session_state_alive(tmp_path, monkeypatch):
    """Pruning tracks session activity, not the last new delivery."""
    import os
    import time

    from gptme.memory.match import SEEN_TTL_SECONDS, _seen_file

    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "mem"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_entry(tmp_path / "mem", "release-policy", keywords=["stable release"])
    write_entry(tmp_path / "mem", "hotfix-policy", keywords=["hotfix"])
    session_a = _hook("cut the stable release", "a")

    # Day 1: session A gets the rule; the delivery is now older than the TTL.
    assert _match_names(session_a, "--dedup-session") == ["release-policy"]
    state_a = _seen_file("a", tmp_path / "state" / "gptme")
    old = time.time() - SEEN_TTL_SECONDS - 3600
    os.utime(state_a, (old, old))
    # Session A stays active: the rule keeps matching (already delivered).
    assert _match_names(session_a, "--dedup-session") == []
    # A new delivery in session B prunes idle state, but A is not idle.
    assert _match_names(_hook("ship the hotfix", "b"), "--dedup-session") == [
        "hotfix-policy"
    ]
    assert state_a.exists()
    assert _match_names(session_a, "--dedup-session") == []


# --- lessons as match candidates (gptme/gptme#4868) --------------------------

#: The rule that failed to surface: keywords cover disabling, not just deleting.
PERSISTENCE_LESSON = """---
match:
  keywords:
    - "delete trajectory"
    - "no-session-persistence"
    - "session persistence"
    - "trajector* persisted"
    - "disable transcripts"
status: active
---
# Trajectory persistence

Never disable or delete session trajectories, including one-shot calls.
"""

#: Text that was actually in play when the rule stayed silent.
INCIDENT_TOOL_INPUT = "add --no-session-persistence to both looks safe ... transcripts"
INCIDENT_PEER_MESSAGE = "--no-session-persistence fits ... 40 MB/day of transcripts"
INCIDENT_USER_PROMPT = (
    "are those one-shot runs still having their trajectories persisted?"
)


def write_lesson(root: Path, rel: str, text: str = PERSISTENCE_LESSON) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def lesson_env(tmp_path, monkeypatch):
    """An empty memory root, isolated dedup state, and one lesson dir."""
    from gptme.lessons.index import clear_cache

    clear_cache()
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "memory"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    lessons = tmp_path / "lessons"
    write_lesson(lessons, "infrastructure/trajectory-persistence.md")
    return lessons


@pytest.mark.parametrize(
    "text", [INCIDENT_TOOL_INPUT, INCIDENT_PEER_MESSAGE, INCIDENT_USER_PROMPT]
)
def test_match_lesson_dir_triggers_on_incident_text(lesson_env, text):
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--prompt", "-", "--lesson-dir", str(lesson_env)]
        + ["--format", "json"],
        input=text,  # plain text on stdin: the peer message starts with "--"
    )
    assert result.exit_code == 0, result.output
    (hit,) = json.loads(result.output)["hits"]
    assert hit["name"] == "trajectory-persistence"
    assert hit["scope"] == hit["type"] == "lesson"
    assert hit["title"] == "Trajectory persistence"
    assert hit["path"].endswith("infrastructure/trajectory-persistence.md")
    assert "Never disable or delete" in hit["body"]
    assert all(m.startswith("keyword:") for m in hit["matched_by"])


def test_match_lesson_fires_on_pretool_payload_once_per_session(lesson_env):
    """Replay: the agent's own SendMessage carries the flag; the rule is injected."""
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "incident",
            "tool_name": "SendMessage",
            "tool_input": {"to": "peer", "message": INCIDENT_TOOL_INPUT},
        }
    )
    args = ["memory", "match", "--prompt", "-", "--format", "hook-json"]
    args += ["--dedup-session", "--lesson-dir", str(lesson_env)]
    first = CliRunner().invoke(util_main, args, input=payload)
    assert first.exit_code == 0, first.output
    out = json.loads(first.output)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert "Never disable or delete" in out["additionalContext"]
    assert "(scope: lesson)" in out["additionalContext"]
    again = CliRunner().invoke(util_main, args, input=payload)
    assert json.loads(again.output)["hookSpecificOutput"]["additionalContext"] == ""


def test_match_without_lesson_flags_ignores_lessons(lesson_env, monkeypatch):
    monkeypatch.chdir(lesson_env.parent)  # ./lessons exists, but is not asked for
    result = CliRunner().invoke(
        util_main, ["memory", "match", INCIDENT_TOOL_INPUT, "--format", "json"]
    )
    assert json.loads(result.output) == {"hits": []}


def test_match_lessons_flag_uses_default_lesson_dirs(lesson_env, monkeypatch):
    monkeypatch.chdir(lesson_env.parent)  # workspace ./lessons is a default dir
    monkeypatch.setattr(Path, "home", lambda: lesson_env.parent / "home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(lesson_env.parent / "config"))
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--lessons", "--format", "json", "--prompt", "-"],
        input=INCIDENT_PEER_MESSAGE,
    )
    assert result.exit_code == 0, result.output
    assert [h["name"] for h in json.loads(result.output)["hits"]] == [
        "trajectory-persistence"
    ]


def test_match_lessons_trigger_on_keywords_and_patterns_only(tmp_path):
    from gptme.lessons.index import clear_cache
    from gptme.memory.match import lesson_candidates, match_memories

    clear_cache()
    lessons = tmp_path / "lessons"
    write_lesson(
        lessons,
        "skills/deploy-helper/SKILL.md",
        "---\nname: deploy-helper\ndescription: Deploys things\n---\n# Deploy\n",
    )
    write_lesson(
        lessons,
        "tools/tooled.md",
        "---\nmatch:\n  keywords: [zebra]\n  tools: [shell]\n---\n# Tooled\n\nBody.\n",
    )
    write_lesson(
        lessons,
        "workflow/patterned.md",
        "---\nmatch:\n  patterns: ['rm\\s+-rf']\n---\n# Patterned\n\nBody.\n",
    )
    write_lesson(
        lessons,
        "workflow/retired.md",
        "---\nmatch:\n  keywords: [zebra]\nstatus: archived\n---\n# Retired\n",
    )
    candidates = lesson_candidates([lessons, tmp_path / "missing"])
    store = MemoryStore([MemoryRoot("project", tmp_path / "memory")])

    def names(text: str) -> list[str]:
        return [h.entry.name for h in match_memories(store, text, lessons=candidates)]

    assert names("use the deploy-helper and the shell tool") == []
    assert names("a zebra") == ["tooled"]
    assert names("then rm  -rf the dir") == ["patterned"]
    assert lesson_candidates([]) == []


def test_match_ranks_memories_and_lessons_together(tmp_path):
    from gptme.lessons.index import clear_cache
    from gptme.memory.match import lesson_candidates, match_memories

    clear_cache()
    memory, lessons = tmp_path / "memory", tmp_path / "lessons"
    write_entry(memory, "mem-one", keywords=["alpha"])
    write_lesson(
        lessons, "a/two.md", "---\nmatch:\n  keywords: [alpha, beta]\n---\n# Two\n"
    )
    write_lesson(lessons, "a/one.md", "---\nmatch:\n  keywords: [alpha]\n---\n# One\n")
    store = MemoryStore([MemoryRoot("project", memory)])
    hits = match_memories(store, "alpha beta", lessons=lesson_candidates([lessons]))
    assert [(h.entry.name, h.entry.scope) for h in hits] == [
        ("two", "lesson"),
        ("mem-one", "project"),
        ("one", "lesson"),
    ]
    # A lesson dir that is also a memory root reports each file once, as memory.
    write_entry(lessons, "dual", keywords=["gamma"], body="Dual.")
    both = MemoryStore([MemoryRoot("project", lessons)])
    dual = match_memories(both, "gamma", lessons=lesson_candidates([lessons]))
    assert [(h.entry.name, h.entry.scope) for h in dual] == [("dual", "project")]


def test_match_lessons_flag_reads_project_config_lesson_dirs(lesson_env, monkeypatch):
    """``[lessons] dirs`` in the workspace gptme.toml count without gptme loading it."""
    workspace = lesson_env.parent / "workspace"
    write_lesson(workspace / "rules", "infrastructure/trajectory-persistence.md")
    (workspace / "gptme.toml").write_text('[lessons]\ndirs = ["rules"]\n')
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(Path, "home", lambda: lesson_env.parent / "home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(lesson_env.parent / "config"))
    result = CliRunner().invoke(
        util_main,
        ["memory", "match", "--lessons", "--format", "json", "--prompt", "-"],
        input=INCIDENT_PEER_MESSAGE,
    )
    assert result.exit_code == 0, result.output
    (hit,) = json.loads(result.output)["hits"]
    assert hit["path"].endswith("rules/infrastructure/trajectory-persistence.md")


def test_match_manifest_skill_keeps_skill_md_triggers(tmp_path):
    """An index.json stub is read in full: SKILL.md patterns and keywords count."""
    from gptme.lessons.index import clear_cache
    from gptme.memory.match import lesson_candidates, match_memories

    clear_cache()
    skills = tmp_path / "skills"
    write_lesson(
        skills,
        "patterned/SKILL.md",
        "---\nname: patterned\ndescription: d\nmatch:\n  patterns: ['rm\\s+-rf']\n"
        "---\n# Patterned\n\nBody.\n",
    )
    write_lesson(
        skills,
        "mixed/SKILL.md",
        "---\nname: mixed\ndescription: d\nmatch:\n  keywords: [okapi]\n"
        "---\n# Mixed\n\nBody.\n",
    )
    (skills / "index.json").write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "patterned", "path": "patterned"},
                    {"name": "mixed", "path": "mixed", "keywords": ["zebra"]},
                ]
            }
        )
    )
    candidates = lesson_candidates([skills])
    assert all(not c.is_stub for c in candidates)
    store = MemoryStore([MemoryRoot("project", tmp_path / "memory")])

    def names(text: str) -> list[str]:
        return [h.entry.name for h in match_memories(store, text, lessons=candidates)]

    assert names("then rm  -rf the dir") == ["patterned"]
    assert names("a zebra") == ["mixed"]
    assert names("an okapi") == ["mixed"]


def test_match_cursor_rules_do_not_trigger(tmp_path):
    """Keywords generated from Cursor globs/alwaysApply are not explicit phrases."""
    from gptme.lessons.index import clear_cache
    from gptme.memory.match import lesson_candidates

    clear_cache()
    cursor = tmp_path / ".cursor"
    write_lesson(
        cursor,
        "rules/python.mdc",
        "---\ndescription: Python style\nglobs: ['**/*.py']\nalwaysApply: true\n"
        "---\n# Python\n\nBody.\n",
    )
    write_lesson(
        cursor, "rules/globbed.md", "---\nglobs: ['**/*.ts']\n---\n# TS\n\nBody.\n"
    )
    assert lesson_candidates([cursor]) == []
