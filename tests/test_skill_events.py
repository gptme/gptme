"""Lifecycle evidence stays append-only, scoped to a run, and non-disruptive."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from gptme.lessons import skill_events
from gptme.lessons.skill_events import (
    EVENTS_FILENAME,
    SkillPhase,
    abandon_skill_invocations,
    read_skill_events,
    record_skill_phase,
    skill_session,
    start_skill_invocation,
)


def _start(logdir: Path, *, session_id: str | None = None) -> str:
    invocation_id = start_skill_invocation(
        logdir, "demo", logdir / "skills" / "demo" / "SKILL.md", session_id=session_id
    )
    assert invocation_id is not None
    return invocation_id


def test_queue_is_not_completion_and_terminal_requires_valid_transition(
    tmp_path: Path,
) -> None:
    invocation_id = _start(tmp_path)
    started_bytes = (tmp_path / EVENTS_FILENAME).read_bytes()

    assert not record_skill_phase(tmp_path, invocation_id, "completed")
    assert not record_skill_phase(tmp_path, invocation_id, "started")
    assert not record_skill_phase(tmp_path, "unknown-id", "failed")
    assert not record_skill_phase(tmp_path, None, "failed")
    assert not record_skill_phase(tmp_path, invocation_id, cast(SkillPhase, "unknown"))
    assert (tmp_path / EVENTS_FILENAME).read_bytes() == started_bytes

    assert record_skill_phase(tmp_path, invocation_id, "queued")
    queued_bytes = (tmp_path / EVENTS_FILENAME).read_bytes()
    assert queued_bytes.startswith(started_bytes)
    assert not record_skill_phase(tmp_path, invocation_id, "queued")
    assert not record_skill_phase(tmp_path, invocation_id, "started")
    events = read_skill_events(tmp_path)
    assert [event.phase for event in events] == ["started", "queued"]
    assert all(event.duration_seconds is None for event in events)

    assert record_skill_phase(tmp_path, invocation_id, "completed")
    finished_bytes = (tmp_path / EVENTS_FILENAME).read_bytes()
    assert finished_bytes.startswith(queued_bytes)
    for phase in ("completed", "failed", "abandoned", "queued", "started"):
        assert not record_skill_phase(tmp_path, invocation_id, cast(SkillPhase, phase))
    assert (tmp_path / EVENTS_FILENAME).read_bytes() == finished_bytes

    first, _, terminal = read_skill_events(tmp_path)
    assert terminal.invocation_id == first.invocation_id == invocation_id
    assert terminal.session_id == first.session_id == str(tmp_path.resolve())
    assert terminal.skill_name == first.skill_name == "demo"
    assert terminal.skill_path == first.skill_path
    assert terminal.surface == first.surface == "gptme-slash-command"
    assert terminal.schema_version == first.schema_version == 1
    elapsed = (
        datetime.fromisoformat(terminal.timestamp)
        - datetime.fromisoformat(first.timestamp)
    ).total_seconds()
    assert terminal.duration_seconds == max(0.0, elapsed)


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("phase", ["failed", "abandoned"])
def test_unfinished_invocation_can_fail_or_be_abandoned(
    tmp_path: Path, queued: bool, phase: SkillPhase
) -> None:
    invocation_id = _start(tmp_path)
    if queued:
        assert record_skill_phase(tmp_path, invocation_id, "queued")

    assert record_skill_phase(tmp_path, invocation_id, phase, error_type="OSError")
    terminal = read_skill_events(tmp_path)[-1]
    assert terminal.phase == phase
    assert terminal.error_type == "OSError"
    assert terminal.duration_seconds is not None
    assert terminal.duration_seconds >= 0


def test_nested_same_conversation_sessions_only_abandon_their_own_invocations(
    tmp_path: Path,
) -> None:
    previous_id = _start(tmp_path, session_id="previous-run")
    assert record_skill_phase(tmp_path, previous_id, "queued")

    with skill_session(tmp_path) as outer_run:
        outer_id = _start(tmp_path)
        with skill_session(tmp_path) as inner_run:
            assert inner_run != outer_run
            inner_id = _start(tmp_path)
            assert record_skill_phase(tmp_path, inner_id, "queued")

        events = read_skill_events(tmp_path)
        assert [e.phase for e in events if e.invocation_id == inner_id] == [
            "started",
            "queued",
            "abandoned",
        ]
        assert [e.phase for e in events if e.invocation_id == outer_id] == ["started"]
        after_inner_id = _start(tmp_path)
        assert read_skill_events(tmp_path)[-1].session_id == outer_run

    events = read_skill_events(tmp_path)
    for invocation_id in (outer_id, after_inner_id):
        assert [e.phase for e in events if e.invocation_id == invocation_id] == [
            "started",
            "abandoned",
        ]
    assert [e.phase for e in events if e.invocation_id == previous_id] == [
        "started",
        "queued",
    ]
    # Reconciliation is safe to repeat and does not sweep another run's history.
    before = (tmp_path / EVENTS_FILENAME).read_bytes()
    abandon_skill_invocations(tmp_path, outer_run)
    abandon_skill_invocations(tmp_path, inner_run)
    assert (tmp_path / EVENTS_FILENAME).read_bytes() == before

    outside_id = _start(tmp_path)
    assert read_skill_events(tmp_path)[-1].invocation_id == outside_id
    assert read_skill_events(tmp_path)[-1].session_id == str(tmp_path.resolve())


def test_session_scope_does_not_capture_another_conversation(tmp_path: Path) -> None:
    own_logdir = tmp_path / "own"
    other_logdir = tmp_path / "other"
    with skill_session(own_logdir):
        _start(own_logdir)
        other_id = _start(other_logdir)

    own_events = read_skill_events(own_logdir)
    assert [e.phase for e in own_events] == ["started", "abandoned"]
    other_events = read_skill_events(other_logdir)
    assert len(other_events) == 1
    assert other_events[0].invocation_id == other_id
    assert other_events[0].session_id == str(other_logdir.resolve())


def test_session_exception_abandons_pending_but_preserves_explicit_terminal(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="provider failed"), skill_session(tmp_path):
        finished_id = _start(tmp_path)
        assert record_skill_phase(tmp_path, finished_id, "queued")
        assert record_skill_phase(tmp_path, finished_id, "completed")
        pending_id = _start(tmp_path)
        raise RuntimeError("provider failed")

    events = read_skill_events(tmp_path)
    assert [e.phase for e in events if e.invocation_id == finished_id] == [
        "started",
        "queued",
        "completed",
    ]
    assert [e.phase for e in events if e.invocation_id == pending_id] == [
        "started",
        "abandoned",
    ]


def test_concurrent_terminal_writers_produce_exactly_one_terminal(
    tmp_path: Path,
) -> None:
    invocation_id = _start(tmp_path)
    assert record_skill_phase(tmp_path, invocation_id, "queued")
    gate = Barrier(3)

    def finish(phase: SkillPhase) -> bool:
        gate.wait(timeout=10)
        return record_skill_phase(tmp_path, invocation_id, phase)

    phases: tuple[SkillPhase, ...] = ("completed", "failed", "abandoned")
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(finish, phases))

    assert sum(results) == 1
    events = read_skill_events(tmp_path)
    assert len(events) == 3
    assert [event.phase for event in events[:2]] == ["started", "queued"]
    assert events[-1].phase == phases[results.index(True)]


@pytest.mark.parametrize("corruption", ["json", "missing-fields", "schema", "phase"])
def test_malformed_history_blocks_appends_without_rewriting_history(
    tmp_path: Path, corruption: str
) -> None:
    invocation_id = _start(tmp_path, session_id="run")
    ledger = tmp_path / EVENTS_FILENAME
    if corruption == "json":
        corrupt_line = '{"invocation_id":'
    elif corruption == "missing-fields":
        corrupt_line = '{"invocation_id":"incomplete"}'
    else:
        record = json.loads(ledger.read_text())
        record["schema_version" if corruption == "schema" else "phase"] = (
            999 if corruption == "schema" else "unknown"
        )
        corrupt_line = json.dumps(record)
    with ledger.open("a") as stream:
        stream.write(corrupt_line + "\n")
    before = ledger.read_bytes()

    with pytest.raises((ValueError, TypeError)):
        read_skill_events(tmp_path)
    assert not record_skill_phase(tmp_path, invocation_id, "failed")
    assert start_skill_invocation(tmp_path, "new", tmp_path / "SKILL.md") is None
    abandon_skill_invocations(tmp_path, "run")
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("failure", ["append", "stat"])
def test_storage_failure_during_close_does_not_mask_session_error_or_leak_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    def broken_append(*args: object) -> None:
        raise OSError("disk full")

    original_exists = Path.exists

    def broken_exists(path: Path) -> bool:
        if path == tmp_path / EVENTS_FILENAME:
            raise PermissionError("conversation directory unreadable")
        return original_exists(path)

    with (
        monkeypatch.context() as patch,
        pytest.raises(RuntimeError, match="provider failed"),
        skill_session(tmp_path),
    ):
        invocation_id = _start(tmp_path)
        assert record_skill_phase(tmp_path, invocation_id, "queued")
        if failure == "append":
            patch.setattr(skill_events, "_append", broken_append)
        else:
            patch.setattr(Path, "exists", broken_exists)
        raise RuntimeError("provider failed")

    assert "Could not reconcile skill invocations" in caplog.text
    assert [event.phase for event in read_skill_events(tmp_path)] == [
        "started",
        "queued",
    ]
    _start(tmp_path)
    assert read_skill_events(tmp_path)[-1].session_id == str(tmp_path.resolve())


def test_start_and_phase_storage_failures_are_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocation_id = _start(tmp_path)
    before = (tmp_path / EVENTS_FILENAME).read_bytes()

    def broken_append(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(skill_events, "_append", broken_append)
    assert start_skill_invocation(tmp_path, "new", tmp_path / "SKILL.md") is None
    assert not record_skill_phase(tmp_path, invocation_id, "queued")
    assert (tmp_path / EVENTS_FILENAME).read_bytes() == before


def test_empty_session_does_not_create_telemetry_artifacts(tmp_path: Path) -> None:
    logdir = tmp_path / "unused"
    with skill_session(logdir):
        pass
    assert not logdir.exists()


def test_complete_record_without_newline_remains_separate_on_append(
    tmp_path: Path,
) -> None:
    invocation_id = start_skill_invocation(tmp_path, "demo", tmp_path / "SKILL.md")
    ledger = tmp_path / EVENTS_FILENAME
    original = ledger.read_bytes().rstrip(b"\n")
    ledger.write_bytes(original)

    assert record_skill_phase(tmp_path, invocation_id, "queued")
    assert ledger.read_bytes().startswith(original + b"\n")
    assert [event.phase for event in read_skill_events(tmp_path)] == [
        "started",
        "queued",
    ]
