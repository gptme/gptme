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
