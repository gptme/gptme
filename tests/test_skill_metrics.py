"""Skill metrics agree with durable lifecycle evidence and bounded usage windows."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gptme import telemetry
from gptme.lessons import skill_events
from gptme.lessons.skill_events import (
    EVENTS_FILENAME,
    SkillEvent,
    SkillPhase,
    abandon_skill_invocations,
    read_skill_events,
    record_skill_phase,
    start_skill_invocation,
)
from gptme.util import _telemetry
from gptme.util.cost_tracker import CostEntry, CostTracker


@pytest.fixture(autouse=True)
def isolated_cost_tracker() -> Iterator[None]:
    token = CostTracker._session_costs_var.set(None)
    try:
        yield
    finally:
        CostTracker._session_costs_var.reset(token)


@pytest.fixture
def metric_reader(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    sdk = pytest.importorskip("opentelemetry.sdk.metrics")
    export = pytest.importorskip("opentelemetry.sdk.metrics.export")
    reader = export.InMemoryMetricReader()
    provider = sdk.MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("skill-lifecycle-test")
    monkeypatch.setattr(_telemetry, "_telemetry_enabled", True)
    monkeypatch.setattr(_telemetry, "TELEMETRY_AVAILABLE", True)
    for key, name in (
        ("skill_invocation_counter", "gptme_skill_invocations"),
        ("skill_completion_counter", "gptme_skill_completions"),
        ("skill_duration_histogram", "gptme_skill_duration_seconds"),
        ("skill_token_counter", "gptme_skill_tokens"),
        ("skill_cost_counter", "gptme_skill_cost_usd"),
    ):
        create = (
            meter.create_histogram
            if key.endswith("histogram")
            else meter.create_counter
        )
        monkeypatch.setattr(_telemetry, f"_{key}", create(name))
    try:
        yield reader
    finally:
        provider.shutdown()


def _metrics(reader: Any) -> dict[str, list[Any]]:
    snapshot = reader.get_metrics_data()
    if snapshot is None:
        return {}
    return {
        metric.name: list(metric.data.data_points)
        for resource in snapshot.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def _record_cost(scale: int = 1) -> None:
    CostTracker.record(
        CostEntry(
            timestamp=0,
            model="test-model",
            input_tokens=11 * scale,
            output_tokens=7 * scale,
            cache_read_tokens=5 * scale,
            cache_creation_tokens=3 * scale,
            cost=0.125 * scale,
        )
    )


def _start(logdir: Path) -> str:
    invocation_id = start_skill_invocation(
        logdir,
        "demo",
        logdir / "private" / "SKILL.md",
        surface="gptme-slash-command",
        session_id="private-session-id",
    )
    assert invocation_id is not None
    return invocation_id


def test_ledger_and_metrics_agree_without_duplicate_terminal_counts(
    tmp_path: Path, metric_reader: Any
) -> None:
    CostTracker.start_session(str(tmp_path.resolve()))
    _record_cost(100)  # Earlier session usage must not be billed to a skill.
    phases: tuple[SkillPhase, ...] = ("completed", "failed", "abandoned")
    for scale, phase in enumerate(phases, 1):
        invocation_id = _start(tmp_path)
        assert record_skill_phase(tmp_path, invocation_id, "queued")
        assert not record_skill_phase(tmp_path, invocation_id, "queued")
        _record_cost(scale)
        if phase == "abandoned":
            abandon_skill_invocations(tmp_path, "private-session-id")
            abandon_skill_invocations(tmp_path, "private-session-id")
        else:
            assert record_skill_phase(
                tmp_path, invocation_id, phase, error_type="private-error-detail"
            )
        for duplicate in phases:
            assert not record_skill_phase(tmp_path, invocation_id, duplicate)

    events = read_skill_events(tmp_path)
    assert len(events) == 9
    terminal = [e for e in events if e.phase in phases]
    assert [e.phase for e in terminal] == list(phases)
    for scale, event in enumerate(terminal, 1):
        assert event.usage == {
            "input_tokens": 11 * scale,
            "output_tokens": 7 * scale,
            "cache_read_tokens": 5 * scale,
            "cache_creation_tokens": 3 * scale,
            "cost_usd": 0.125 * scale,
            "requests": 1,
        }
    assert all(e.usage is None for e in events if e.phase in {"started", "queued"})

    metrics = _metrics(metric_reader)
    assert set(metrics) == {
        "gptme_skill_invocations",
        "gptme_skill_completions",
        "gptme_skill_duration_seconds",
        "gptme_skill_tokens",
        "gptme_skill_cost_usd",
    }
    invocation = metrics["gptme_skill_invocations"]
    assert len(invocation) == 1
    assert invocation[0].value == 3
    completions = metrics["gptme_skill_completions"]
    assert {p.attributes["status"]: p.value for p in completions} == {
        "completed": 1,
        "failed": 1,
        "abandoned": 1,
    }
    assert all(p.attributes["usage_available"] is True for p in completions)
    durations = metrics["gptme_skill_duration_seconds"]
    assert len(durations) == 1
    assert durations[0].count == 3
    assert durations[0].sum == pytest.approx(
        sum(e.duration_seconds or 0 for e in terminal)
    )
    assert {
        p.attributes["token_type"]: p.value for p in metrics["gptme_skill_tokens"]
    } == {
        "input_tokens": 66,
        "output_tokens": 42,
        "cache_read_tokens": 30,
        "cache_creation_tokens": 18,
    }
    costs = metrics["gptme_skill_cost_usd"]
    assert len(costs) == 1
    assert costs[0].value == pytest.approx(0.75)

    # Cardinality stays bounded: IDs, local paths, and error detail never label metrics.
    for name, points in metrics.items():
        expected = {"skill_name", "surface"}
        if name == "gptme_skill_completions":
            expected |= {"status", "usage_available"}
        elif name == "gptme_skill_tokens":
            expected.add("token_type")
        for point in points:
            assert set(point.attributes) == expected
            assert point.attributes["skill_name"] == "demo"
            assert point.attributes["surface"] == "gptme-slash-command"


@pytest.mark.parametrize("tracking", ["missing", "unrelated", "reset", "restarted"])
def test_unknown_usage_is_not_exported_as_zero(
    tmp_path: Path, metric_reader: Any, tracking: str
) -> None:
    if tracking != "missing":
        logdir = tmp_path / "other" if tracking == "unrelated" else tmp_path
        CostTracker.start_session(str(logdir.resolve()))
        _record_cost()
    invocation_id = _start(tmp_path)
    started = read_skill_events(tmp_path)[0]
    assert record_skill_phase(tmp_path, invocation_id, "queued")
    if tracking == "reset":
        CostTracker.reset()
    elif tracking == "restarted":
        CostTracker.start_session(str(tmp_path.resolve()))
        _record_cost(100)  # Even increasing totals cannot hide a tracker reset.
        costs = CostTracker.get_session_costs()
        assert costs is not None
        assert costs.tracking_id != started.cost_tracker_id
    assert record_skill_phase(tmp_path, invocation_id, "completed")
    assert read_skill_events(tmp_path)[-1].usage is None
    if tracking in {"missing", "unrelated"}:
        assert started.cost_baseline is None
        assert started.cost_tracker_id is None
    metrics = _metrics(metric_reader)
    assert "gptme_skill_tokens" not in metrics
    assert "gptme_skill_cost_usd" not in metrics
    completion = metrics["gptme_skill_completions"][0]
    assert completion.value == 1
    assert completion.attributes["usage_available"] is False


def test_real_zero_usage_is_distinguished_from_missing_usage(tmp_path: Path) -> None:
    CostTracker.start_session(str(tmp_path.resolve()))
    invocation_id = _start(tmp_path)
    assert record_skill_phase(tmp_path, invocation_id, "queued")
    assert record_skill_phase(tmp_path, invocation_id, "completed")
    usage = read_skill_events(tmp_path)[-1].usage
    assert usage is not None
    assert set(usage) == {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cost_usd",
        "requests",
    }
    assert all(value == 0 for value in usage.values())


def test_export_happens_only_after_successful_durable_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[SkillEvent] = []

    def observe(event: SkillEvent) -> None:
        assert read_skill_events(tmp_path)[-1] == event
        emitted.append(event)

    monkeypatch.setattr(telemetry, "record_skill_event", observe)
    invocation_id = _start(tmp_path)
    assert record_skill_phase(tmp_path, invocation_id, "queued")
    assert not record_skill_phase(tmp_path, invocation_id, "queued")

    def broken_append(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(skill_events, "_append", broken_append)
    assert not record_skill_phase(tmp_path, invocation_id, "completed")
    assert start_skill_invocation(tmp_path, "demo", tmp_path / "SKILL.md") is None
    assert [event.phase for event in emitted] == ["started", "queued"]


@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
def test_telemetry_failure_does_not_change_persistence_or_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    def broken_export(event: SkillEvent) -> None:
        raise error("exporter unavailable")

    monkeypatch.setattr(telemetry, "record_skill_event", broken_export)
    invocation_id = _start(tmp_path)
    assert record_skill_phase(tmp_path, invocation_id, "queued")
    assert record_skill_phase(tmp_path, invocation_id, "completed")
    assert not record_skill_phase(tmp_path, invocation_id, "completed")
    assert [event.phase for event in read_skill_events(tmp_path)] == [
        "started",
        "queued",
        "completed",
    ]


def test_v1_history_remains_unchanged_when_v2_terminal_is_appended(
    tmp_path: Path,
) -> None:
    legacy = {
        "invocation_id": "legacy-invocation",
        "session_id": "legacy-run",
        "skill_name": "demo",
        "skill_path": str(tmp_path / "SKILL.md"),
        "surface": "gptme-slash-command",
        "phase": "started",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "duration_seconds": None,
        "error_type": None,
        "schema_version": 1,
    }
    original = (
        json.dumps(legacy) + "\n" + json.dumps({**legacy, "phase": "queued"}) + "\n"
    ).encode()
    ledger = tmp_path / EVENTS_FILENAME
    ledger.write_bytes(original)
    CostTracker.start_session(str(tmp_path.resolve()))
    _record_cost()

    assert record_skill_phase(tmp_path, "legacy-invocation", "completed")
    assert ledger.read_bytes().startswith(original)
    started, queued, completed = read_skill_events(tmp_path)
    assert started.schema_version == queued.schema_version == 1
    assert completed.schema_version == 2
    assert completed.phase == "completed"
    assert completed.usage is None
    assert completed.cost_baseline is None


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -100.0])
@pytest.mark.parametrize("when", ["before-start", "before-terminal"])
def test_invalid_accounting_remains_unknown(
    tmp_path: Path, metric_reader: Any, cost: float, when: str
) -> None:
    CostTracker.start_session(str(tmp_path.resolve()))
    _record_cost()
    bad_entry = CostEntry(
        timestamp=0,
        model="test-model",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost=cost,
    )
    if when == "before-start":
        CostTracker.record(bad_entry)
    invocation_id = _start(tmp_path)
    assert record_skill_phase(tmp_path, invocation_id, "queued")
    if when == "before-terminal":
        CostTracker.record(bad_entry)
    assert record_skill_phase(tmp_path, invocation_id, "completed")
    assert read_skill_events(tmp_path)[-1].usage is None
    metrics = _metrics(metric_reader)
    assert "gptme_skill_tokens" not in metrics
    assert "gptme_skill_cost_usd" not in metrics
    assert metrics["gptme_skill_completions"][0].attributes["usage_available"] is False


def test_decreasing_accounting_is_unknown_even_when_totals_are_positive(
    tmp_path: Path, metric_reader: Any
) -> None:
    CostTracker.start_session(str(tmp_path.resolve()))
    _record_cost(100)
    invocation_id = _start(tmp_path)
    assert record_skill_phase(tmp_path, invocation_id, "queued")
    _record_cost(-1)
    assert record_skill_phase(tmp_path, invocation_id, "completed")
    assert read_skill_events(tmp_path)[-1].usage is None
    metrics = _metrics(metric_reader)
    assert "gptme_skill_tokens" not in metrics
    assert "gptme_skill_cost_usd" not in metrics
