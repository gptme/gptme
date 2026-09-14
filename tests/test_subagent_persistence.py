"""Tests for subagent registry rehydration and meta persistence."""

import json
import threading
from pathlib import Path

import pytest

from gptme.logmanager import Log
from gptme.message import Message
from gptme.tools.subagent.persistence import (
    META_FILENAME,
    _dict_to_subagent,
    _subagent_to_dict,
    load_subagent_by_id,
    load_subagent_meta,
    persist_subagent_meta,
    remove_subagent_meta,
    scan_rehydrate_subagents,
)
from gptme.tools.subagent.types import Subagent


def _completed_log(logdir: Path, content: str = "done") -> None:
    logdir.mkdir(parents=True, exist_ok=True)
    Log([Message("assistant", f"```complete\n{content}\n```")]).write_jsonl(
        logdir / "conversation.jsonl"
    )


class TestSubagentToDict:
    def test_roundtrip_fields(self, tmp_path: Path):
        sa = Subagent(
            agent_id="test-agent",
            prompt="hello",
            thread=None,
            logdir=tmp_path,
            model="gpt-4",
            execution_mode="thread",
            isolated=False,
        )
        restored = _dict_to_subagent(_subagent_to_dict(sa))
        assert restored.agent_id == "test-agent"
        assert restored.prompt == "hello"
        assert restored.logdir == tmp_path
        assert restored.model == "gpt-4"
        assert restored.execution_mode == "thread"
        assert restored.thread is None
        assert restored.process is None

    def test_skips_runtime_only_fields(self, tmp_path: Path):
        thread = threading.Thread(target=lambda: None)
        sa = Subagent(
            agent_id="test-agent",
            prompt="hello",
            thread=thread,
            logdir=tmp_path,
            model=None,
        )
        data = _subagent_to_dict(sa)
        assert "thread" not in data
        assert "process" not in data
        assert "cancel_event" not in data
        assert "prompt_queue_closed" not in data
        restored = _dict_to_subagent(data)
        assert restored.thread is None

    def test_path_fields_converted_to_strings(self, tmp_path: Path):
        sa = Subagent(
            agent_id="test-agent",
            prompt="hello",
            thread=None,
            logdir=tmp_path,
            model=None,
            workdir=tmp_path / "workspace",
        )
        data = _subagent_to_dict(sa)
        assert isinstance(data["logdir"], str)
        assert isinstance(data["workdir"], str)

    def test_none_fields_omitted(self, tmp_path: Path):
        sa = Subagent(
            agent_id="test-agent",
            prompt="hello",
            thread=None,
            logdir=tmp_path,
            model=None,
            workdir=None,
        )
        data = _subagent_to_dict(sa)
        assert data["model"] is None
        assert "workdir" not in data

    def test_dict_output_schema_roundtrips(self, tmp_path: Path):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        sa = Subagent(
            agent_id="schema-agent",
            prompt="hello",
            thread=None,
            logdir=tmp_path,
            model=None,
            output_schema=schema,
        )
        restored = _dict_to_subagent(_subagent_to_dict(sa))
        assert restored.output_schema == schema

    def test_type_output_schema_omitted(self, tmp_path: Path):
        sa = Subagent(
            agent_id="type-schema",
            prompt="hello",
            thread=None,
            logdir=tmp_path,
            model=None,
            output_schema=dict,
        )
        data = _subagent_to_dict(sa)
        assert "output_schema" not in data

    def test_started_at_coerced_to_float(self, tmp_path: Path):
        restored = _dict_to_subagent(
            {
                "agent_id": "typed",
                "prompt": "hello",
                "logdir": str(tmp_path),
                "model": None,
                "started_at": "12.5",
            }
        )
        assert restored.started_at == 12.5

    def test_invalid_started_at_falls_back_to_zero(self, tmp_path: Path):
        restored = _dict_to_subagent(
            {
                "agent_id": "bad-time",
                "prompt": "hello",
                "logdir": str(tmp_path),
                "model": None,
                "started_at": "abc",
            }
        )
        assert restored.started_at == 0.0


class TestPersistAndLoad:
    def test_persist_creates_file(self, tmp_path: Path):
        sa = Subagent(
            agent_id="persist-agent",
            prompt="do thing",
            thread=None,
            logdir=tmp_path,
            model="gpt-4",
        )
        persist_subagent_meta(sa)
        data = json.loads((tmp_path / META_FILENAME).read_text())
        assert data["agent_id"] == "persist-agent"
        assert "thread" not in data

    def test_load_restores_subagent(self, tmp_path: Path):
        sa = Subagent(
            agent_id="load-agent",
            prompt="do thing",
            thread=None,
            logdir=tmp_path,
            model="gpt-4",
            execution_mode="subprocess",
            isolated=True,
        )
        persist_subagent_meta(sa)
        loaded = load_subagent_meta(tmp_path)
        assert loaded is not None
        assert loaded.agent_id == "load-agent"
        assert loaded.execution_mode == "subprocess"
        assert loaded.isolated is True

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert load_subagent_meta(tmp_path) is None

    def test_remove_deletes_file(self, tmp_path: Path):
        sa = Subagent(
            agent_id="remove-agent",
            prompt="do thing",
            thread=None,
            logdir=tmp_path,
            model=None,
        )
        persist_subagent_meta(sa)
        remove_subagent_meta(tmp_path)
        assert not (tmp_path / META_FILENAME).exists()

    def test_remove_idempotent(self, tmp_path: Path):
        remove_subagent_meta(tmp_path)

    def test_load_corrupt_returns_none(self, tmp_path: Path):
        (tmp_path / META_FILENAME).write_text("not json")
        assert load_subagent_meta(tmp_path) is None

    def test_persist_keeps_previous_file_if_replace_fails(
        self, tmp_path: Path, monkeypatch
    ):
        from gptme.tools.subagent import persistence as persistence_mod

        persist_subagent_meta(
            Subagent(
                agent_id="atomic-agent",
                prompt="old",
                thread=None,
                logdir=tmp_path,
                model=None,
            )
        )
        original = (tmp_path / META_FILENAME).read_text()

        def boom(_src, _dest):
            raise OSError("simulated crash after temp write")

        monkeypatch.setattr(persistence_mod.os, "replace", boom)
        persist_subagent_meta(
            Subagent(
                agent_id="atomic-agent",
                prompt="new",
                thread=None,
                logdir=tmp_path,
                model=None,
            )
        )
        assert (tmp_path / META_FILENAME).read_text() == original
        assert list(tmp_path.glob(".subagent-meta-*.tmp")) == []


class TestScanRehydrate:
    def test_scans_logs_dir(self, tmp_path: Path):
        subagent_dir = tmp_path / "subagent-test-1"
        _completed_log(subagent_dir)
        persist_subagent_meta(
            Subagent(
                agent_id="test-1",
                prompt="hello",
                thread=None,
                logdir=subagent_dir,
                model="gpt-4",
            )
        )
        rehydrated = scan_rehydrate_subagents(tmp_path)
        assert len(rehydrated) == 1
        assert rehydrated[0].agent_id == "test-1"

    def test_skips_without_conversation_jsonl(self, tmp_path: Path):
        subagent_dir = tmp_path / "subagent-test-2"
        subagent_dir.mkdir()
        persist_subagent_meta(
            Subagent(
                agent_id="test-2",
                prompt="hello",
                thread=None,
                logdir=subagent_dir,
                model=None,
            )
        )
        assert scan_rehydrate_subagents(tmp_path) == []

    def test_skips_non_subagent_dirs(self, tmp_path: Path):
        other_dir = tmp_path / "conversation-abc"
        other_dir.mkdir()
        (other_dir / "conversation.jsonl").write_text("")
        (other_dir / META_FILENAME).write_text("{}")
        assert scan_rehydrate_subagents(tmp_path) == []

    def test_skips_unreadable_meta(self, tmp_path: Path):
        subagent_dir = tmp_path / "subagent-test-3"
        _completed_log(subagent_dir)
        (subagent_dir / META_FILENAME).write_text("not json")
        assert scan_rehydrate_subagents(tmp_path) == []

    def test_raises_on_logs_dir_iterdir_oserror(self, tmp_path: Path, monkeypatch):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        real_iterdir = Path.iterdir

        def boom(self: Path):
            if self == logs_dir:
                raise OSError("transient")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", boom)
        with pytest.raises(OSError, match="transient"):
            scan_rehydrate_subagents(logs_dir)

    def test_load_by_id(self, tmp_path: Path):
        subagent_dir = tmp_path / "subagent-by-id"
        _completed_log(subagent_dir)
        persist_subagent_meta(
            Subagent(
                agent_id="by-id",
                prompt="hello",
                thread=None,
                logdir=subagent_dir,
                model=None,
            )
        )
        loaded = load_subagent_by_id("by-id", tmp_path)
        assert loaded is not None
        assert loaded.agent_id == "by-id"
        assert load_subagent_by_id("missing", tmp_path) is None

    def test_load_by_id_finds_random_suffix_logdir(self, tmp_path: Path):
        subagent_dir = tmp_path / "subagent-worker-a7k2"
        _completed_log(subagent_dir)
        persist_subagent_meta(
            Subagent(
                agent_id="worker",
                prompt="hello",
                thread=None,
                logdir=subagent_dir,
                model=None,
                started_at=100.0,
            )
        )
        loaded = load_subagent_by_id("worker", tmp_path)
        assert loaded is not None
        assert loaded.logdir == subagent_dir
        assert load_subagent_by_id("work", tmp_path) is None

    def test_load_by_id_prefers_newest_started_at(self, tmp_path: Path):
        older = tmp_path / "subagent-worker-old1"
        newer = tmp_path / "subagent-worker-new2"
        _completed_log(older, "old")
        _completed_log(newer, "new")
        persist_subagent_meta(
            Subagent(
                agent_id="worker",
                prompt="old",
                thread=None,
                logdir=older,
                model=None,
                started_at=10.0,
            )
        )
        persist_subagent_meta(
            Subagent(
                agent_id="worker",
                prompt="new",
                thread=None,
                logdir=newer,
                model=None,
                started_at=20.0,
            )
        )
        loaded = load_subagent_by_id("worker", tmp_path)
        assert loaded is not None
        assert loaded.prompt == "new"
        assert loaded.logdir == newer

    def test_load_by_id_skips_invalid_started_at(self, tmp_path: Path):
        older = tmp_path / "subagent-worker-old1"
        newer = tmp_path / "subagent-worker-new2"
        _completed_log(older, "old")
        _completed_log(newer, "new")
        persist_subagent_meta(
            Subagent(
                agent_id="worker",
                prompt="old",
                thread=None,
                logdir=older,
                model=None,
                started_at=10.0,
            )
        )
        persist_subagent_meta(
            Subagent(
                agent_id="worker",
                prompt="new",
                thread=None,
                logdir=newer,
                model=None,
                started_at=20.0,
            )
        )
        data = json.loads((older / META_FILENAME).read_text())
        data["started_at"] = "abc"
        (older / META_FILENAME).write_text(json.dumps(data))
        loaded = load_subagent_by_id("worker", tmp_path)
        assert loaded is not None
        assert loaded.prompt == "new"


class TestRegistryRehydration:
    def setup_method(self):
        import gptme.tools.subagent.types as types_mod
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        types_mod._registry_rehydrated = False
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def teardown_method(self):
        import gptme.tools.subagent.types as types_mod
        from gptme.tools.subagent.types import (
            _subagent_results,
            _subagent_results_lock,
            _subagents,
            _subagents_lock,
        )

        types_mod._registry_rehydrated = False
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()

    def test_find_subagent_rehydrates_from_disk(self, tmp_path: Path, monkeypatch):
        from gptme.tools.subagent.api import _find_subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        logs_dir = tmp_path / "logs"
        subagent_dir = logs_dir / "subagent-rehydrated-agent"
        _completed_log(subagent_dir, "hello from disk")
        persist_subagent_meta(
            Subagent(
                agent_id="rehydrated-agent",
                prompt="do thing",
                thread=None,
                logdir=subagent_dir,
                model="gpt-4",
            )
        )
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        found = _find_subagent("rehydrated-agent")
        assert found is not None
        assert found.agent_id == "rehydrated-agent"
        with _subagents_lock:
            assert any(s.agent_id == "rehydrated-agent" for s in _subagents)

    def test_status_works_after_simulated_restart(self, tmp_path: Path, monkeypatch):
        from gptme.tools.subagent.api import subagent_status

        logs_dir = tmp_path / "logs"
        subagent_dir = logs_dir / "subagent-status-agent-ab12"
        _completed_log(subagent_dir, "all good")
        persist_subagent_meta(
            Subagent(
                agent_id="status-agent",
                prompt="do thing",
                thread=None,
                logdir=subagent_dir,
                model=None,
            )
        )
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        status = subagent_status("status-agent")
        assert status["status"] == "success"
        assert "all good" in (status["result"] or "")

    def test_continue_works_after_simulated_restart(self, tmp_path: Path, monkeypatch):
        import gptme.tools.subagent.execution as subagent_execution
        from gptme.tools.subagent.api import subagent_continue
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        logs_dir = tmp_path / "logs"
        subagent_dir = logs_dir / "subagent-continue-agent-cd34"
        _completed_log(subagent_dir, "first result")
        persist_subagent_meta(
            Subagent(
                agent_id="continue-agent",
                prompt="do thing",
                thread=None,
                logdir=subagent_dir,
                model=None,
                execution_mode="thread",
            )
        )
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        captured: dict = {}

        def fake_create_subagent_thread(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(
            subagent_execution, "_create_subagent_thread", fake_create_subagent_thread
        )

        subagent_continue("continue-agent", "follow up")
        with _subagents_lock:
            continued = next(s for s in _subagents if s.agent_id == "continue-agent")
        assert continued.thread is not None
        continued.thread.join(timeout=1)
        assert captured["resume"] is True
        assert captured["prompt"] == "follow up"
        assert captured["logdir"] == subagent_dir
        assert (subagent_dir / META_FILENAME).exists()

    def test_list_rehydrates_completed_agents(self, tmp_path: Path, monkeypatch):
        from gptme.tools.subagent.api import subagent_list

        logs_dir = tmp_path / "logs"
        subagent_dir = logs_dir / "subagent-listed-agent"
        _completed_log(subagent_dir, "listed")
        persist_subagent_meta(
            Subagent(
                agent_id="listed-agent",
                prompt="do thing",
                thread=None,
                logdir=subagent_dir,
                model=None,
            )
        )
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        ids = [entry["agent_id"] for entry in subagent_list()]
        assert "listed-agent" in ids

    def test_list_keeps_newest_when_id_is_reused(self, tmp_path: Path, monkeypatch):
        from gptme.tools.subagent.api import subagent_list

        logs_dir = tmp_path / "logs"
        older = logs_dir / "subagent-worker-old1"
        newer = logs_dir / "subagent-worker-new2"
        _completed_log(older, "old")
        _completed_log(newer, "new")
        persist_subagent_meta(
            Subagent(
                agent_id="worker",
                prompt="old run",
                thread=None,
                logdir=older,
                model=None,
                started_at=10.0,
            )
        )
        persist_subagent_meta(
            Subagent(
                agent_id="worker",
                prompt="new run",
                thread=None,
                logdir=newer,
                model=None,
                started_at=20.0,
            )
        )
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        listed = [e for e in subagent_list() if e["agent_id"] == "worker"]
        assert len(listed) == 1
        assert listed[0]["prompt_preview"] == "new run"

    def test_list_retries_after_failed_scan(self, tmp_path: Path, monkeypatch):
        import gptme.tools.subagent.types as types_mod
        from gptme.tools.subagent.api import subagent_list

        logs_dir = tmp_path / "logs"
        subagent_dir = logs_dir / "subagent-retry-agent"
        _completed_log(subagent_dir, "later")
        persist_subagent_meta(
            Subagent(
                agent_id="retry-agent",
                prompt="do thing",
                thread=None,
                logdir=subagent_dir,
                model=None,
            )
        )
        monkeypatch.setattr("gptme.dirs.get_logs_dir", lambda: logs_dir)

        calls = {"n": 0}
        real_iterdir = Path.iterdir

        def flaky(self: Path):
            if self == logs_dir:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("transient")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", flaky)

        assert subagent_list() == []
        assert types_mod._registry_rehydrated is False
        ids = [entry["agent_id"] for entry in subagent_list()]
        assert "retry-agent" in ids
        assert types_mod._registry_rehydrated is True

    def test_find_subagent_prefers_newer_in_memory(self, tmp_path: Path):
        from gptme.tools.subagent.api import _find_subagent
        from gptme.tools.subagent.types import _subagents, _subagents_lock

        stale = Subagent(
            agent_id="reuse-id",
            prompt="old",
            thread=None,
            logdir=tmp_path / "old",
            model=None,
        )
        live = Subagent(
            agent_id="reuse-id",
            prompt="new",
            thread=None,
            logdir=tmp_path / "new",
            model=None,
        )
        with _subagents_lock:
            _subagents.extend([stale, live])
        found = _find_subagent("reuse-id")
        assert found is live
