"""Deterministic review regressions for gptme/gptme#4843."""

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gptme.tools.subagent import api, persistence, types


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    def clear() -> None:
        with types._subagents_lock:
            types._subagents.clear()
        with types._subagent_results_lock:
            types._subagent_results.clear()
        types._registry_rehydrated = False

    clear()
    yield
    clear()


@pytest.mark.parametrize("lookup", ["id", "list"])
def test_restore_cannot_poison_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lookup: str
) -> None:
    logdir = tmp_path / "subagent-worker-ab12"
    logdir.mkdir()
    (logdir / "conversation.jsonl").write_text("")
    old = types.Subagent("worker", "task", None, logdir, None)
    persistence.persist_subagent_meta(old)
    persistence.persist_subagent_outcome(old, types.ReturnType("timeout", "old"))
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    outcome_loaded = threading.Event()
    finish_restore = threading.Event()
    execution_started = threading.Event()
    finish_execution = threading.Event()
    real_load = persistence.load_subagent_outcome
    errors: list[BaseException] = []

    def delayed_load(sa: types.Subagent) -> types.ReturnType | None:
        result = real_load(sa)
        if threading.current_thread() is lookup_thread:
            outcome_loaded.set()
            assert finish_restore.wait(5)
        return result

    def execute(**kwargs: Any) -> None:
        execution_started.set()
        assert finish_execution.wait(5)

    def lookup_worker() -> None:
        try:
            if lookup == "id":
                api._find_subagent("worker")
            else:
                api._maybe_rehydrate_registry()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(persistence, "load_subagent_outcome", delayed_load)
    monkeypatch.setattr(api._exec, "_create_subagent_thread", execute)
    lookup_thread = threading.Thread(target=lookup_worker)
    lookup_thread.start()
    continued = None
    try:
        assert outcome_loaded.wait(5)
        api.subagent_continue("worker", "continue")
        assert execution_started.wait(5)
        with types._subagents_lock:
            continued = types._subagents[-1]
        finish_restore.set()
        lookup_thread.join(5)
        assert not lookup_thread.is_alive()
        assert not errors
        assert continued.status().status == "running"
    finally:
        finish_restore.set()
        finish_execution.set()
        lookup_thread.join(5)
        if continued and continued.thread:
            continued.thread.join(5)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "running"),
        ("status", "unknown"),
        ("status", []),
        ("input_tokens", "10"),
        ("output_tokens", 1.5),
        ("tool_uses", True),
        ("duration_s", "3"),
        ("duration_s", float("nan")),
        ("duration_s", float("inf")),
        ("duration_s", 10**400),
        ("generation", 10**400),
    ],
)
def test_malformed_saved_fields_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    sa = types.Subagent("worker", "task", None, tmp_path, None)
    persistence.persist_subagent_outcome(sa, types.ReturnType("timeout", "old"))
    path = tmp_path / persistence.OUTCOME_FILENAME
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    assert persistence.load_subagent_outcome(sa) is None


def test_persist_snapshots_result_under_registry_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persistence snapshot cannot pair an old result with a new generation."""

    class CheckedResults(dict[str, types.ReturnType]):
        def get(self, key: str, default: Any = None) -> Any:
            assert types._subagents_lock.locked()
            return super().get(key, default)

    sa = types.Subagent("worker", "task", None, tmp_path, None)
    with types._subagents_lock:
        types._subagents.append(sa)
    result = types.ReturnType("cancelled", "old")
    monkeypatch.setattr(types, "_subagent_results", CheckedResults(worker=result))
    types._persist_cached_outcome("worker")
    assert persistence.load_subagent_outcome(sa) == result


def test_failed_continuation_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sa = types.Subagent("worker", "task", None, tmp_path, None)
    (tmp_path / "conversation.jsonl").write_text("")
    with types._subagents_lock:
        types._subagents.append(sa)
    persistence.persist_subagent_meta(sa)
    types.set_subagent_result_if_absent("worker", types.ReturnType("success", "old"))

    def fail(**kwargs: Any) -> None:
        raise RuntimeError("continuation failed before a new message")

    monkeypatch.setattr(api._exec, "_create_subagent_thread", fail)
    api.subagent_continue("worker", "follow up")
    with types._subagents_lock:
        current = types._subagents[-1]
    assert current.thread is not None
    current.thread.join(5)
    assert not current.thread.is_alive()
    outcome = persistence.load_subagent_outcome(current)
    assert outcome is not None
    assert outcome.status == "failure"
    assert outcome.result == "continuation failed before a new message"


def test_spawn_clears_restored_result_at_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    cli = importlib.import_module("gptme.cli.main")
    models = importlib.import_module("gptme.llm.models")
    profiles = importlib.import_module("gptme.profiles")
    old = types.Subagent("worker", "old", None, tmp_path / "old", None)
    with types._subagents_lock:
        types._subagents.append(old)
    persistence.persist_subagent_outcome(old, types.ReturnType("timeout", "old"))
    monkeypatch.setattr(models, "get_default_model", lambda: None)
    monkeypatch.setattr(profiles, "get_profile", lambda _: None)

    def get_logdir(name: str) -> Path:
        # Restoration lands after spawn's initial cache clear, before publication.
        assert persistence.restore_subagent_outcome(old)
        return tmp_path / name

    monkeypatch.setattr(cli, "get_logdir", get_logdir)
    executed = threading.Event()
    monkeypatch.setattr(
        api._exec, "_create_subagent_thread", lambda **kw: executed.set()
    )
    api.subagent("worker", "new", use_subprocess=False)
    with types._subagents_lock:
        current = types._subagents[-1]
    assert current.thread is not None
    current.thread.join(5)
    assert not current.thread.is_alive()
    assert executed.is_set()


@pytest.mark.parametrize("terminal_status", ["cancelled", "timeout"])
def test_continuation_notifies_winning_durable_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_status: types.Status
) -> None:
    """A late continuation failure must not contradict a terminal winner."""
    sa = types.Subagent("worker", "task", None, tmp_path, None)
    (tmp_path / "conversation.jsonl").write_text("")
    with types._subagents_lock:
        types._subagents.append(sa)
    started = threading.Event()
    finish = threading.Event()
    notifications: list[tuple[str, str]] = []

    def execute(**kwargs: Any) -> None:
        started.set()
        assert finish.wait(5)
        raise RuntimeError("late execution failure")

    def notify(agent_id: str, status: str, summary: str, **kwargs: Any) -> None:
        notifications.append((status, summary))

    monkeypatch.setattr(api._exec, "_create_subagent_thread", execute)
    monkeypatch.setattr(api, "notify_completion", notify)
    api.subagent_continue("worker", "follow up")
    with types._subagents_lock:
        current = types._subagents[-1]
    try:
        assert started.wait(5)
        winner = types.ReturnType(terminal_status, "orchestrator stopped the attempt")
        assert types.set_subagent_result_if_absent("worker", winner)
    finally:
        finish.set()
        assert current.thread is not None
        current.thread.join(5)
    assert not current.thread.is_alive()
    assert persistence.load_subagent_outcome(current) == winner
    assert len(notifications) == 1
    assert notifications[0][0] == terminal_status
    assert "orchestrator stopped the attempt" in notifications[0][1]


@pytest.mark.parametrize("lookup", ["id", "list"])
def test_queued_cancellation_survives_restart_without_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lookup: str
) -> None:
    """Cancellation can finish before a queued worker creates its log."""
    logdir = tmp_path / "subagent-worker-ab12"
    finish = threading.Event()
    thread = threading.Thread(target=finish.wait)
    thread.start()
    sa = types.Subagent("worker", "queued task", thread, logdir, None)
    with types._subagents_lock:
        types._subagents.append(sa)
    persistence.persist_subagent_meta(sa)
    try:
        assert "cancelled" in api.subagent_cancel("worker")
    finally:
        finish.set()
        thread.join(5)
    assert not (logdir / "conversation.jsonl").exists()
    assert persistence.load_subagent_outcome(sa) is not None
    with types._subagents_lock:
        types._subagents.clear()
    with types._subagent_results_lock:
        types._subagent_results.clear()
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    if lookup == "list":
        assert [(s["agent_id"], s["status"]) for s in api.subagent_list()] == [
            ("worker", "cancelled")
        ]
    assert api.subagent_status("worker")["status"] == "cancelled"


@pytest.mark.parametrize("lookup", ["id", "list"])
def test_concurrent_lookup_never_observes_unrestored_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lookup: str
) -> None:
    logdir = tmp_path / "subagent-worker-ab12"
    logdir.mkdir()
    (logdir / "conversation.jsonl").write_text("")
    sa = types.Subagent("worker", "task", None, logdir, None)
    persistence.persist_subagent_meta(sa)
    persistence.persist_subagent_outcome(sa, types.ReturnType("timeout", "deadline"))
    monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: tmp_path)
    loading = threading.Event()
    finish = threading.Event()
    real_load = persistence.load_subagent_outcome
    errors: list[BaseException] = []

    def delayed_load(sa: types.Subagent) -> types.ReturnType | None:
        outcome = real_load(sa)
        if threading.current_thread() is first:
            loading.set()
            assert finish.wait(5)
        return outcome

    def first_lookup() -> None:
        try:
            if lookup == "id":
                api.subagent_status("worker")
            else:
                api.subagent_list()
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=first_lookup)
    monkeypatch.setattr(persistence, "load_subagent_outcome", delayed_load)
    first.start()
    try:
        assert loading.wait(5)
        assert api.subagent_status("worker")["status"] == "timeout"
    finally:
        finish.set()
        first.join(5)
    assert not first.is_alive()
    assert not errors
    assert api.subagent_status("worker")["status"] == "timeout"
