"""Acceptance tests for the retained-worktree slice (gptme/gptme#4555, AT-5).

No inference; all subagents are simulated or cancelled before they produce
LLM calls.  Tests verify:

- retain_worktree=True skips auto-cleanup after the subagent finishes.
- subagent_release() performs a safe GC and returns {"status": "released"}.
- A dirty/uncommitted worktree returns {"status": "cleanup_required"}.
- retention_deadline expiry revokes continuation eligibility.
- Retained workspace survives parent restart (persist/rehydrate round-trip).
- Idempotent GC: calling subagent_release() twice is safe.
- Unsupported (non-retained) isolated subagent reports ineligibility.
- retain_worktree=True without isolated=True raises ValueError.
"""

import queue
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from gptme.tools.subagent import execution as _exec
from gptme.tools.subagent import subagent_release
from gptme.tools.subagent.persistence import (
    persist_subagent_meta,
)
from gptme.tools.subagent.types import (
    ReturnType,
    Subagent,
    _completion_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    def clear() -> None:
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()
        while True:
            try:
                _completion_queue.get_nowait()
            except queue.Empty:
                break

    clear()
    yield
    clear()


def _make_subagent(
    logdir: Path,
    *,
    agent_id: str = "retain-test",
    retain_worktree: bool = True,
    retention_deadline: float | None = None,
    worktree_path: Path | None = None,
    isolated: bool = True,
) -> Subagent:
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / "conversation.jsonl").touch()
    sa = Subagent(
        agent_id=agent_id,
        prompt="test task",
        thread=None,
        logdir=logdir,
        model=None,
        isolated=isolated,
        retain_worktree=retain_worktree,
        retention_deadline=retention_deadline,
        worktree_path=worktree_path,
        execution_mode="subprocess",
    )
    with _subagents_lock:
        _subagents.append(sa)
    return sa


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRetainWorktreeSkipsAutoCleanup:
    def test_cleanup_isolation_skips_when_retain_true(self, tmp_path: Path) -> None:
        """_cleanup_isolation must leave the worktree intact when retain_worktree=True."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / "artifact.txt").write_text("important work")
        sa = _make_subagent(
            tmp_path / "logdir",
            worktree_path=worktree,
        )
        result = _exec._cleanup_isolation(sa)
        assert result is None
        assert worktree.exists(), (
            "worktree must not be deleted when retain_worktree=True"
        )
        assert (worktree / "artifact.txt").exists()

    def test_cleanup_isolation_runs_normally_without_retain(
        self, tmp_path: Path
    ) -> None:
        """_cleanup_isolation removes the worktree when retain_worktree=False."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()  # empty non-git dir: cleanup will delete it
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()
        sa = Subagent(
            agent_id="no-retain",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
            isolated=True,
            retain_worktree=False,
            worktree_path=worktree,
            repo_path=None,
            execution_mode="subprocess",
        )
        # Cleanup should NOT short-circuit (retain_worktree=False), so the
        # empty non-git directory should be deleted.
        _exec._cleanup_isolation(sa)
        assert not worktree.exists(), "cleanup should have deleted the empty worktree"


class TestSubagentRelease:
    def test_release_absent_worktree_is_idempotent(self, tmp_path: Path) -> None:
        """subagent_release() on an already-gone worktree returns released with path=None."""
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        sa = _make_subagent(logdir, worktree_path=tmp_path / "gone")
        # Mark as finished
        with _subagent_results_lock:
            _subagent_results[sa.agent_id] = ReturnType("success", "done")
        result = subagent_release(sa.agent_id)
        assert result["status"] == "released"
        assert result["path"] is None

    def test_release_raises_when_still_running(self, tmp_path: Path) -> None:
        """subagent_release() must refuse if the subagent is still running."""
        import threading

        logdir = tmp_path / "logdir"
        logdir.mkdir()
        blocker = threading.Event()
        t = threading.Thread(target=blocker.wait, daemon=True)
        t.start()
        sa = Subagent(
            agent_id="running-retain",
            prompt="test",
            thread=t,
            logdir=logdir,
            model=None,
            isolated=True,
            retain_worktree=True,
            worktree_path=tmp_path / "wt",
            execution_mode="thread",
        )
        (logdir / "conversation.jsonl").touch()
        with _subagents_lock:
            _subagents.append(sa)
        with pytest.raises(ValueError, match="still running"):
            subagent_release(sa.agent_id)
        blocker.set()
        t.join(timeout=2)

    def test_release_raises_when_not_retained(self, tmp_path: Path) -> None:
        """subagent_release() must refuse subagents not launched with retain_worktree=True."""
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        sa = _make_subagent(logdir, retain_worktree=False)
        with _subagent_results_lock:
            _subagent_results[sa.agent_id] = ReturnType("success", "done")
        with pytest.raises(ValueError, match="retain_worktree=True"):
            subagent_release(sa.agent_id)

    def test_release_raises_when_not_found(self, tmp_path: Path) -> None:
        """subagent_release() raises for an unknown agent_id."""
        with pytest.raises(ValueError, match="not found"):
            subagent_release("nonexistent-agent")

    def test_release_clean_worktree_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A clean (no uncommitted changes) worktree is GC'd and status=released."""
        from gptme.tools.subagent import execution as ex
        from gptme.util import git_worktree as gwt

        worktree = tmp_path / "wt"
        worktree.mkdir()
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        sa = _make_subagent(logdir, worktree_path=worktree)
        with _subagent_results_lock:
            _subagent_results[sa.agent_id] = ReturnType("success", "done")

        # Pretend it's a clean worktree (no git needed)
        monkeypatch.setattr(gwt, "worktree_is_clean", lambda path: True)
        # Pretend _cleanup_isolation_forced deletes the dir
        deleted: list[Path] = []

        def fake_cleanup(s: Subagent) -> None:
            if s.worktree_path and s.worktree_path.exists():
                import shutil

                shutil.rmtree(s.worktree_path)
            deleted.append(s.worktree_path or Path("?"))

        monkeypatch.setattr(ex, "_cleanup_isolation_forced", fake_cleanup)

        result = subagent_release(sa.agent_id)
        assert result["status"] == "released"
        assert str(worktree) in result["path"]
        assert deleted

    def test_release_dirty_worktree_returns_cleanup_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dirty worktree must return cleanup_required rather than deleting."""
        from gptme.util import git_worktree as gwt

        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / "uncommitted.txt").write_text("changes")
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        sa = _make_subagent(logdir, worktree_path=worktree)
        with _subagent_results_lock:
            _subagent_results[sa.agent_id] = ReturnType("success", "done")

        monkeypatch.setattr(gwt, "worktree_is_clean", lambda path: False)

        result = subagent_release(sa.agent_id)
        assert result["status"] == "cleanup_required"
        assert result["owner"] == sa.agent_id
        assert str(worktree) in result["path"]
        assert worktree.exists(), "dirty worktree must be preserved"

    def test_release_force_deletes_dirty_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """force=True must bypass the dirty check and proceed with cleanup."""
        from gptme.tools.subagent import execution as ex
        from gptme.util import git_worktree as gwt

        worktree = tmp_path / "wt"
        worktree.mkdir()
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        sa = _make_subagent(logdir, worktree_path=worktree)
        with _subagent_results_lock:
            _subagent_results[sa.agent_id] = ReturnType("success", "done")

        monkeypatch.setattr(gwt, "worktree_is_clean", lambda path: False)
        deleted: list[bool] = []

        def fake_cleanup(s: Subagent) -> None:
            deleted.append(True)

        monkeypatch.setattr(ex, "_cleanup_isolation_forced", fake_cleanup)

        result = subagent_release(sa.agent_id, force=True)
        assert result["status"] == "released"
        assert deleted

    def test_release_idempotent_second_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling subagent_release() twice must succeed on the second call too."""
        from gptme.tools.subagent import execution as ex
        from gptme.util import git_worktree as gwt

        worktree = tmp_path / "wt"
        worktree.mkdir()
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        sa = _make_subagent(logdir, worktree_path=worktree)
        with _subagent_results_lock:
            _subagent_results[sa.agent_id] = ReturnType("success", "done")

        monkeypatch.setattr(gwt, "worktree_is_clean", lambda path: True)

        def fake_cleanup(s: Subagent) -> None:
            if s.worktree_path and s.worktree_path.exists():
                import shutil

                shutil.rmtree(s.worktree_path)

        monkeypatch.setattr(ex, "_cleanup_isolation_forced", fake_cleanup)

        r1 = subagent_release(sa.agent_id)
        r2 = subagent_release(sa.agent_id)
        assert r1["status"] == "released"
        # Second call: worktree already gone → idempotent released with path=None
        assert r2["status"] == "released"
        assert r2["path"] is None


class TestRetentionDeadline:
    def test_continuation_blocked_after_deadline(self, tmp_path: Path) -> None:
        """continuation_blocker must block when retention_deadline has passed."""
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        past = time.time() - 1.0
        sa = _make_subagent(
            logdir, retention_deadline=past, worktree_path=tmp_path / "wt"
        )
        blocker = sa.continuation_blocker()
        assert blocker is not None
        assert "expired" in blocker.lower() or "deadline" in blocker.lower()

    def test_continuation_allowed_before_deadline(self, tmp_path: Path) -> None:
        """continuation_blocker must allow when retention_deadline is in the future."""
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()
        future = time.time() + 3600.0
        worktree = tmp_path / "wt"
        worktree.mkdir()
        sa = _make_subagent(logdir, retention_deadline=future, worktree_path=worktree)
        blocker = sa.continuation_blocker()
        assert blocker is None, f"Expected no blocker, got: {blocker!r}"

    def test_continuation_allowed_with_no_deadline(self, tmp_path: Path) -> None:
        """retention_deadline=None means no expiry; continuation must be allowed."""
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()
        worktree = tmp_path / "wt"
        worktree.mkdir()
        sa = _make_subagent(logdir, retention_deadline=None, worktree_path=worktree)
        blocker = sa.continuation_blocker()
        assert blocker is None, f"Expected no blocker, got: {blocker!r}"

    def test_continuation_blocked_when_worktree_gone(self, tmp_path: Path) -> None:
        """continuation_blocker must block when the retained worktree no longer exists."""
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()
        # worktree_path points to a non-existent directory
        sa = _make_subagent(logdir, worktree_path=tmp_path / "deleted-wt")
        blocker = sa.continuation_blocker()
        assert blocker is not None
        assert "no longer exists" in blocker.lower() or "released" in blocker.lower()


class TestNonRetainedIsolationStillBlocked:
    def test_non_retained_isolated_subagent_is_ineligible(self, tmp_path: Path) -> None:
        """An isolated subagent without retain_worktree=True must still be ineligible."""
        logdir = tmp_path / "logdir"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()
        sa = _make_subagent(
            logdir, retain_worktree=False, worktree_path=tmp_path / "wt"
        )
        blocker = sa.continuation_blocker()
        assert blocker is not None
        assert "isolated workspace" in blocker.lower()


class TestRetainRequiresIsolation:
    def test_retain_without_isolated_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """subagent(retain_worktree=True) without isolation must raise ValueError."""
        monkeypatch.setattr("gptme.tools.subagent.api._find_subagent", lambda _: None)

        with pytest.raises(ValueError, match="retain_worktree=True requires"):
            from gptme.tools.subagent.api import subagent as _subagent

            _subagent(
                "retain-no-iso",
                "task",
                retain_worktree=True,
                isolated=False,
            )


class TestRetentionPersistence:
    def test_retain_fields_round_trip_through_persistence(self, tmp_path: Path) -> None:
        """retain_worktree and retention_deadline survive a persist/load round-trip."""
        from gptme.tools.subagent.persistence import (
            load_subagent_by_id,
        )

        logdir = tmp_path / "subagent-persist-test-ab12"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()
        deadline = time.time() + 7200.0
        worktree = tmp_path / "wt"
        worktree.mkdir()

        sa = Subagent(
            agent_id="persist-test",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
            isolated=True,
            retain_worktree=True,
            retention_deadline=deadline,
            worktree_path=worktree,
            execution_mode="subprocess",
        )
        persist_subagent_meta(sa)

        loaded = load_subagent_by_id("persist-test", tmp_path)
        assert loaded is not None
        assert loaded.retain_worktree is True
        assert loaded.retention_deadline is not None
        assert abs(loaded.retention_deadline - deadline) < 0.01

    def test_continuation_still_allowed_after_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rehydrated retained subagent can still be continued before deadline."""
        from gptme.tools.subagent.persistence import (
            load_subagent_by_id,
        )

        logdir = tmp_path / "subagent-restart-test-cd34"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()
        worktree = tmp_path / "wt"
        worktree.mkdir()
        future = time.time() + 3600.0

        sa = Subagent(
            agent_id="restart-test",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
            isolated=True,
            retain_worktree=True,
            retention_deadline=future,
            worktree_path=worktree,
            execution_mode="subprocess",
        )
        persist_subagent_meta(sa)

        rehydrated = load_subagent_by_id("restart-test", tmp_path)
        assert rehydrated is not None
        blocker = rehydrated.continuation_blocker()
        assert blocker is None, (
            f"Rehydrated retained subagent blocked unexpectedly: {blocker!r}"
        )


class TestRetainFieldsPreservedOnContinue:
    def test_continue_copies_retain_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """subagent_continue must copy retain_worktree and retention_deadline."""
        from gptme.tools.subagent.api import subagent_continue as _continue
        from gptme.tools.subagent.types import _subagents as _reg

        # Prevent the continuation thread from actually running; we only need to
        # verify that the registry entry is populated with the correct fields.
        monkeypatch.setattr("threading.Thread.start", lambda self: None)

        worktree = tmp_path / "wt"
        worktree.mkdir()
        logdir = tmp_path / "logdir-continue-retain"
        logdir.mkdir()
        (logdir / "conversation.jsonl").touch()

        deadline = time.time() + 7200.0
        sa = Subagent(
            agent_id="continue-retain",
            prompt="test",
            thread=None,
            logdir=logdir,
            model=None,
            isolated=True,
            retain_worktree=True,
            retention_deadline=deadline,
            worktree_path=worktree,
            execution_mode="thread",
        )
        with _subagents_lock:
            _reg.append(sa)
        with _subagent_results_lock:
            _subagent_results[sa.agent_id] = ReturnType("success", "done")

        # subagent_continue creates the continuation Subagent and registers it.
        # The thread.start() call is mocked out above so no real work runs.
        _continue(sa.agent_id, "follow-up task")

        with _subagents_lock:
            entries = [s for s in _reg if s.agent_id == sa.agent_id]
        assert entries, "continued subagent must still be in the registry"
        continued = entries[-1]
        assert continued.retain_worktree is True, (
            "retain_worktree must be copied to the continued attempt"
        )
        assert continued.retention_deadline is not None
        assert abs(continued.retention_deadline - deadline) < 0.01, (
            "retention_deadline must be copied to the continued attempt"
        )


class TestRetainValidationOrderingWithRoles:
    def test_retain_with_isolation_param_does_not_raise_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """retain_worktree=True with isolation='worktree' (no explicit isolated=True) is valid.

        Previously the retention check fired before isolation="worktree" was
        translated to isolated=True, so callers had to pass both. The fix moves
        the check after the translation so only one is needed.
        """
        monkeypatch.setattr("gptme.tools.subagent.api._find_subagent", lambda _: None)
        # Prevent real git worktree creation; return a tmp dir instead.
        monkeypatch.setattr(
            "gptme.util.git_worktree.create_worktree",
            lambda *a, **kw: tmp_path / "mock-wt",
        )
        (tmp_path / "mock-wt").mkdir(exist_ok=True)
        # Prevent the launch thread from starting; we only test validation.
        monkeypatch.setattr("threading.Thread.start", lambda self: None)

        from gptme.tools.subagent.api import subagent as _subagent

        # Must NOT raise about retain_worktree validation; any other exception
        # (from actually trying to launch) is acceptable.
        try:
            _subagent(
                "retain-with-isolation-param",
                "task",
                retain_worktree=True,
                isolation="worktree",
            )
        except ValueError as e:
            assert "retain_worktree=True requires" not in str(e), (
                "retain_worktree validation must not fire when isolation='worktree' is set"
            )
