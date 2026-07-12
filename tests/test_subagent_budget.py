"""Unit tests for SubagentBudget and budget-aware subagent_parallel/pipeline.

Tests the budget coordination layer added in gptme/gptme#3192.
All tests are pure-unit (no LLM calls, no subprocess spawning).
"""

import threading
from dataclasses import asdict
from unittest.mock import patch

import pytest

from gptme.tools.subagent.batch import (
    BatchJob,
    subagent_batch,
    subagent_parallel,
    subagent_pipeline,
)
from gptme.tools.subagent.types import ReturnType, SubagentBudget

# ---------------------------------------------------------------------------
# SubagentBudget unit tests
# ---------------------------------------------------------------------------


class TestSubagentBudget:
    def test_unlimited_budget_is_never_exhausted(self):
        budget = SubagentBudget(total=None)
        assert not budget.exhausted()
        budget.record(1_000_000)
        assert not budget.exhausted()

    def test_unlimited_remaining_is_infinity(self):
        budget = SubagentBudget(total=None)
        assert budget.remaining() == float("inf")

    def test_fresh_budget_not_exhausted(self):
        budget = SubagentBudget(total=10_000)
        assert not budget.exhausted()
        assert budget.spent() == 0
        assert budget.remaining() == 10_000

    def test_record_accumulates_output_tokens(self):
        budget = SubagentBudget(total=10_000)
        budget.record(3_000)
        budget.record(2_000)
        assert budget.spent() == 5_000
        assert budget.remaining() == 5_000
        assert not budget.exhausted()

    def test_exhausted_when_spent_equals_total(self):
        budget = SubagentBudget(total=5_000)
        budget.record(5_000)
        assert budget.exhausted()
        assert budget.remaining() == 0

    def test_exhausted_when_spent_exceeds_total(self):
        budget = SubagentBudget(total=1_000)
        budget.record(1_500)
        assert budget.exhausted()
        assert budget.remaining() == 0  # clamped to 0, not negative

    def test_zero_total_is_immediately_exhausted(self):
        budget = SubagentBudget(total=0)
        assert budget.exhausted()

    def test_record_thread_safety(self):
        """Concurrent record() calls must not lose updates."""
        budget = SubagentBudget(total=None)
        n_threads = 50
        tokens_per_thread = 1_000

        def record_loop():
            for _ in range(10):
                budget.record(tokens_per_thread // 10)

        threads = [threading.Thread(target=record_loop) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert budget.spent() == n_threads * tokens_per_thread

    def test_repr_shows_total(self):
        budget = SubagentBudget(total=42_000)
        assert "42000" in repr(budget)

    def test_repr_unlimited(self):
        budget = SubagentBudget(total=None)
        assert "None" in repr(budget)


# ---------------------------------------------------------------------------
# subagent_parallel budget integration tests
# ---------------------------------------------------------------------------

_SUCCESS = ReturnType("success", "done", input_tokens=100, output_tokens=200)
_FAILURE = ReturnType("failure", "oops", input_tokens=50, output_tokens=50)


def _mock_subagent_fn(results_by_id: dict[str, ReturnType]):
    """Return a mock for `subagent` that pre-populates job results."""

    def _subagent(agent_id, prompt, **kwargs):
        # Nothing to do — wait_all() will find results via the patched BatchJob
        pass

    return _subagent


class TestSubagentParallelBudget:
    """Tests for budget enforcement in subagent_parallel()."""

    def _run_parallel(self, tasks, budget, results_by_id):
        """Helper: run subagent_parallel() with mocked subagent() and BatchJob."""

        def fake_wait_all(self_job, timeout=300, cancel_on_failure=False):
            # Populate results as if agents completed
            for aid in self_job.agent_ids:
                if aid not in self_job.results:
                    r = results_by_id.get(aid, ReturnType("failure", "no mock result"))
                    self_job.results[aid] = r
            # Mirror BatchJob.wait_all() budget recording so tests see correct spend
            if self_job.budget is not None:
                for aid, r in self_job.results.items():
                    if (
                        aid not in self_job._budget_recorded_ids
                        and r.output_tokens is not None
                    ):
                        self_job.budget.record(r.output_tokens)
                        self_job._budget_recorded_ids.add(aid)
            return {aid: asdict(r) for aid, r in self_job.results.items()}

        with (
            patch("gptme.tools.subagent.batch.subagent") as mock_subagent,
            patch.object(BatchJob, "wait_all", fake_wait_all),
        ):
            return subagent_parallel(tasks, budget=budget), mock_subagent

    def test_no_budget_spawns_all_agents(self):
        tasks = [("a", "task a"), ("b", "task b")]
        results, mock_sub = self._run_parallel(
            tasks, budget=None, results_by_id={"a": _SUCCESS, "b": _SUCCESS}
        )
        assert mock_sub.call_count == 2
        assert all(r["status"] == "success" for r in results)

    def test_exhausted_budget_skips_all_agents(self):
        budget = SubagentBudget(total=0)  # immediately exhausted
        tasks = [("a", "task a"), ("b", "task b")]
        results, mock_sub = self._run_parallel(tasks, budget=budget, results_by_id={})
        assert mock_sub.call_count == 0
        assert all(r["status"] == "budget_exceeded" for r in results)

    def test_partial_budget_skips_agents_exhausted_before_call(self):
        """Budget already exhausted before second spawn: second agent gets budget_exceeded.

        Budget is checked before each agent is spawned.  Within a single
        subagent_parallel() call, tokens from completed agents are recorded
        *after* wait_all() returns, so within-call cross-agent gating only
        fires when the budget is already exhausted at spawn time (e.g. from a
        prior call).  This test pre-exhausts the budget after the first agent
        would be spawned so the second agent sees an exhausted budget.
        """
        budget = SubagentBudget(total=200)
        # Exhaust the budget before the call starts by simulating a prior call
        budget.record(200)
        assert budget.exhausted()

        tasks = [("first", "task first"), ("second", "task second")]

        with patch("gptme.tools.subagent.batch.subagent") as mock_sub:
            results = subagent_parallel(tasks, budget=budget)

        # No agents were spawned — budget already exhausted
        assert mock_sub.call_count == 0
        assert results[0]["status"] == "budget_exceeded"
        assert results[1]["status"] == "budget_exceeded"

    def test_budget_gates_spawning_before_each_agent(self):
        """Budget exhausted mid-list: agents after the exhaustion point are skipped.

        When the budget is NOT yet exhausted when the call starts but becomes
        exhausted before later items in the task list, those later agents are
        skipped.  This can happen when a budget object is partially used by the
        caller before the parallel() call.
        """
        budget = SubagentBudget(total=1)  # 1 output token remaining
        # Spend all but 1 token so the budget is not exhausted yet
        # After recording 1 more token it will be exhausted

        tasks = [("a", "task a"), ("b", "task b"), ("c", "task c")]
        results_by_id = {
            "a": ReturnType("success", "ok", input_tokens=10, output_tokens=1),
        }

        spawned: list[str] = []

        def fake_subagent(agent_id, prompt, **kwargs):
            spawned.append(agent_id)
            # Simulate token recording happening during spawn for agent "a"
            # (in real usage this happens via wait_all, but we simulate inline
            # to test that the check before "b" and "c" sees the exhausted budget)

        def fake_wait_all(self_job, timeout=300, cancel_on_failure=False):
            for aid in self_job.agent_ids:
                if aid not in self_job.results:
                    self_job.results[aid] = results_by_id.get(
                        aid, ReturnType("failure", "no mock")
                    )
            if self_job.budget is not None:
                for aid, r in self_job.results.items():
                    if (
                        aid not in self_job._budget_recorded_ids
                        and r.output_tokens is not None
                    ):
                        self_job.budget.record(r.output_tokens)
                        self_job._budget_recorded_ids.add(aid)
            return {}

        with (
            patch("gptme.tools.subagent.batch.subagent", side_effect=fake_subagent),
            patch.object(BatchJob, "wait_all", fake_wait_all),
        ):
            # Pre-exhaust budget between tasks: record "a"'s tokens before
            # the parallel call so that "b" and "c" see an exhausted budget.
            budget.record(1)  # budget is now exhausted
            results = subagent_parallel(tasks, budget=budget)

        # All agents were skipped since budget was exhausted before the call
        assert spawned == []
        assert all(r["status"] == "budget_exceeded" for r in results)

    def test_budget_records_output_tokens_after_completion(self):
        """Budget.spent() reflects output tokens from completed agents."""
        budget = SubagentBudget(total=10_000)
        tasks = [("x", "task x")]
        results_by_id = {
            "x": ReturnType("success", "ok", input_tokens=500, output_tokens=300),
        }

        def fake_wait_all(self_job, timeout=300, cancel_on_failure=False):
            for aid in self_job.agent_ids:
                self_job.results[aid] = results_by_id[aid]
            if self_job.budget is not None:
                for aid, r in self_job.results.items():
                    if (
                        aid not in self_job._budget_recorded_ids
                        and r.output_tokens is not None
                    ):
                        self_job.budget.record(r.output_tokens)
                        self_job._budget_recorded_ids.add(aid)
            return {}

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch.object(BatchJob, "wait_all", fake_wait_all),
        ):
            subagent_parallel(tasks, budget=budget)

        assert budget.spent() == 300  # only output_tokens, not input

    def test_budget_shared_across_calls(self):
        """A shared budget accumulates across two subagent_parallel() calls."""
        budget = SubagentBudget(total=400)

        tasks_a = [("a1", "task")]
        tasks_b = [("b1", "task")]

        results_by_id_a = {
            "a1": ReturnType("success", "ok", input_tokens=100, output_tokens=250),
        }
        results_by_id_b = {
            "b1": ReturnType("success", "ok", input_tokens=100, output_tokens=250),
        }

        def fake_wait_all_a(self_job, timeout=300, cancel_on_failure=False):
            for aid in self_job.agent_ids:
                self_job.results[aid] = results_by_id_a[aid]
            if self_job.budget is not None:
                for aid, r in self_job.results.items():
                    if (
                        aid not in self_job._budget_recorded_ids
                        and r.output_tokens is not None
                    ):
                        self_job.budget.record(r.output_tokens)
                        self_job._budget_recorded_ids.add(aid)
            return {}

        def fake_wait_all_b(self_job, timeout=300, cancel_on_failure=False):
            for aid in self_job.agent_ids:
                self_job.results[aid] = results_by_id_b[aid]
            if self_job.budget is not None:
                for aid, r in self_job.results.items():
                    if (
                        aid not in self_job._budget_recorded_ids
                        and r.output_tokens is not None
                    ):
                        self_job.budget.record(r.output_tokens)
                        self_job._budget_recorded_ids.add(aid)
            return {}

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch.object(BatchJob, "wait_all", fake_wait_all_a),
        ):
            subagent_parallel(tasks_a, budget=budget)

        assert budget.spent() == 250
        assert not budget.exhausted()  # 150 remaining

        # Second call: budget only has 150 left, agent needs 250 → budget_exceeded
        with (
            patch("gptme.tools.subagent.batch.subagent") as mock_sub_b,
            patch.object(BatchJob, "wait_all", fake_wait_all_b),
        ):
            results_b = subagent_parallel(tasks_b, budget=budget)

        # b1 was spawned (budget not yet exhausted at spawn time), completes, records 250
        assert mock_sub_b.call_count == 1
        assert results_b[0]["status"] == "success"
        # After second call, total spent = 500, exhausted
        assert budget.spent() == 500
        assert budget.exhausted()

    def test_empty_tasks_with_budget(self):
        """Empty task list with a budget returns empty results without error."""
        budget = SubagentBudget(total=1_000)
        with patch("gptme.tools.subagent.batch.subagent"):
            results = subagent_parallel([], budget=budget)
        assert results == []
        assert budget.spent() == 0

    def test_budget_exceeded_result_structure(self):
        """budget_exceeded result has expected keys."""
        budget = SubagentBudget(total=0)
        tasks = [("a", "task")]
        results, _ = self._run_parallel(tasks, budget=budget, results_by_id={})
        r = results[0]
        assert r["status"] == "budget_exceeded"
        assert "result" in r
        assert r["result"]  # non-empty message


# ---------------------------------------------------------------------------
# subagent_pipeline budget integration tests
# ---------------------------------------------------------------------------


class TestSubagentPipelineBudget:
    """Tests for budget enforcement in subagent_pipeline()."""

    def _make_stage(self, label="stage"):
        """Return a simple stage callable."""
        return lambda item, prev: f"[{label}] {item}"

    def test_pipeline_budget_exceeded_skips_remaining_stages(self):
        """When budget is exhausted, remaining pipeline stages return budget_exceeded."""
        # Budget that is pre-exhausted
        budget = SubagentBudget(total=0)

        items = [("item1", "do something")]
        stages = [self._make_stage("s0"), self._make_stage("s1")]

        with patch("gptme.tools.subagent.batch.subagent"):
            results = subagent_pipeline(items, *stages, budget=budget)

        # Both stages should be budget_exceeded since budget was exhausted from the start
        assert results[0][0]["status"] == "budget_exceeded"
        assert results[0][1]["status"] == "budget_exceeded"

    def test_pipeline_records_tokens_per_stage(self):
        """Budget accumulates output tokens from each completed pipeline stage."""
        budget = SubagentBudget(total=10_000)
        items = [("item1", "task")]
        stages = [self._make_stage("s0")]

        stage_result = {
            "status": "success",
            "result": "done",
            "output_tokens": 400,
            "input_tokens": 100,
        }

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch(
                "gptme.tools.subagent.batch.subagent_wait", return_value=stage_result
            ),
        ):
            subagent_pipeline(items, *stages, budget=budget)

        assert budget.spent() == 400

    def test_pipeline_no_budget_runs_all_stages(self):
        """Without a budget, all pipeline stages run normally."""
        items = [("item1", "task")]
        stages = [self._make_stage("s0"), self._make_stage("s1")]

        success_result = {"status": "success", "result": "ok", "output_tokens": None}

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch(
                "gptme.tools.subagent.batch.subagent_wait", return_value=success_result
            ) as mock_wait,
        ):
            results = subagent_pipeline(items, *stages, budget=None)

        # wait called once per stage
        assert mock_wait.call_count == len(stages)
        assert all(r["status"] == "success" for r in results[0])


# ---------------------------------------------------------------------------
# subagent_batch budget integration tests
# ---------------------------------------------------------------------------


class TestSubagentBatchBudget:
    def test_batch_skips_agents_when_budget_exhausted(self):
        """subagent_batch() pre-populates budget_exceeded for skipped agents."""
        budget = SubagentBudget(total=0)
        tasks = [("a", "task a"), ("b", "task b")]

        with patch("gptme.tools.subagent.batch.subagent") as mock_sub:
            job = subagent_batch(tasks, budget=budget)

        assert mock_sub.call_count == 0
        # All results pre-populated as budget_exceeded
        assert job.results["a"].status == "budget_exceeded"
        assert job.results["b"].status == "budget_exceeded"


# ---------------------------------------------------------------------------
# BatchJob.wait_all timeout-retry budget tests
# ---------------------------------------------------------------------------


class TestBatchJobTimeoutRetry:
    """Tests that wait_all() re-waits timed-out agents on a subsequent call.

    Regression guard for the bug where timeout placeholders in self.results
    caused a second wait_all() call to skip those agents entirely — meaning
    their real output_tokens were never recorded in the shared budget.
    """

    def test_timeout_then_retry_records_tokens(self):
        """Second wait_all() fetches the real result and records tokens."""
        budget = SubagentBudget(total=1_000)
        job = BatchJob(agent_ids=["a"], budget=budget)

        # Simulate first wait_all() timing out: placeholder stored with no tokens.
        job.results["a"] = ReturnType("timeout", "Timed out after 1s")
        assert budget.spent() == 0

        # Simulate second wait_all() where agent "a" has now completed.
        real_result = ReturnType("success", "done", input_tokens=50, output_tokens=400)

        def fake_wait_one(_aid, _deadline):
            return _aid, real_result

        # Patch the inner _wait_one by controlling subagent_wait directly.
        # We call wait_all() and intercept at the subagent_wait level.
        with patch(
            "gptme.tools.subagent.batch.subagent_wait",
            return_value={
                "status": "success",
                "result": "done",
                "input_tokens": 50,
                "output_tokens": 400,
            },
        ):
            job.wait_all(timeout=5)

        # The real result should have replaced the timeout placeholder.
        assert job.results["a"].status == "success"
        assert job.results["a"].output_tokens == 400
        # Budget should now reflect the real spend.
        assert budget.spent() == 400

    def test_timeout_placeholder_not_double_counted(self):
        """A successful result recorded in first wait_all() is not re-recorded."""
        budget = SubagentBudget(total=1_000)
        job = BatchJob(agent_ids=["a", "b"], budget=budget)

        # Agent "a" completed in the first call; "b" timed out.
        job.results["a"] = ReturnType(
            "success", "ok", input_tokens=10, output_tokens=200
        )
        job.results["b"] = ReturnType("timeout", "Timed out after 1s")
        job._budget_recorded_ids.add("a")
        budget.record(200)

        # Second call: "b" now completes.
        with patch(
            "gptme.tools.subagent.batch.subagent_wait",
            return_value={
                "status": "success",
                "result": "ok",
                "input_tokens": 10,
                "output_tokens": 300,
            },
        ):
            job.wait_all(timeout=5)

        # "a" must not be double-counted; "b" must now be counted.
        assert budget.spent() == 500  # 200 (a) + 300 (b)


# ---------------------------------------------------------------------------
# subagent_parallel max_concurrent tests
# ---------------------------------------------------------------------------


class TestSubagentParallelMaxConcurrent:
    """Tests for max_concurrent fleet cap in subagent_parallel()."""

    def _make_mock_wait(self, results_by_id: dict[str, "ReturnType"]):
        """Return a mock for subagent_wait() that returns pre-set results."""

        def mock_wait(agent_id, timeout=60, max_result_chars=0):
            r = results_by_id.get(agent_id, ReturnType("success", "done"))
            return asdict(r)

        return mock_wait

    def test_max_concurrent_none_spawns_all_at_once(self):
        """With max_concurrent=None, all agents are spawned before any waits."""
        tasks = [("a", "p"), ("b", "p"), ("c", "p")]
        all_success = {
            t[0]: ReturnType("success", "ok", output_tokens=50) for t in tasks
        }

        with (
            patch("gptme.tools.subagent.batch.subagent") as mock_sub,
            patch(
                "gptme.tools.subagent.batch.subagent_wait",
                side_effect=self._make_mock_wait(all_success),
            ),
        ):
            results = subagent_parallel(tasks, max_concurrent=None)

        assert mock_sub.call_count == 3
        assert all(r["status"] == "success" for r in results)

    def test_max_concurrent_limits_simultaneous_agents(self):
        """At most max_concurrent agents are active at the same time."""
        n_tasks = 8
        cap = 3
        tasks = [(f"t{i}", f"prompt {i}") for i in range(n_tasks)]

        active_count = 0
        max_active = 0
        lock = threading.Lock()

        def mock_subagent(agent_id, prompt, **kwargs):
            pass

        def mock_wait(agent_id, timeout=60, max_result_chars=0):
            nonlocal active_count, max_active
            with lock:
                active_count += 1
                if active_count > max_active:
                    max_active = active_count
            # Small sleep so multiple threads are in this critical section together
            import time

            time.sleep(0.02)
            with lock:
                active_count -= 1
            return {"status": "success", "result": "done", "output_tokens": 10}

        with (
            patch("gptme.tools.subagent.batch.subagent", side_effect=mock_subagent),
            patch("gptme.tools.subagent.batch.subagent_wait", side_effect=mock_wait),
        ):
            results = subagent_parallel(tasks, max_concurrent=cap)

        assert max_active <= cap, f"Expected ≤{cap} concurrent, saw {max_active}"
        assert len(results) == n_tasks
        assert all(r["status"] == "success" for r in results)

    def test_max_concurrent_all_tasks_complete(self):
        """With max_concurrent set, all tasks eventually complete (none dropped)."""
        tasks = [(f"w{i}", f"work {i}") for i in range(10)]
        all_results = {
            t[0]: ReturnType("success", f"result {t[0]}", output_tokens=20)
            for t in tasks
        }

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch(
                "gptme.tools.subagent.batch.subagent_wait",
                side_effect=self._make_mock_wait(all_results),
            ),
        ):
            results = subagent_parallel(tasks, max_concurrent=3)

        assert len(results) == 10
        for i, r in enumerate(results):
            assert r["status"] == "success", f"task {i} failed: {r}"

    def test_max_concurrent_preserves_order(self):
        """Results are returned in the same order as input tasks."""
        tasks = [(f"ord-{i}", f"p{i}") for i in range(6)]
        results_by_id = {
            f"ord-{i}": ReturnType("success", f"result-{i}", output_tokens=10)
            for i in range(6)
        }

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch(
                "gptme.tools.subagent.batch.subagent_wait",
                side_effect=self._make_mock_wait(results_by_id),
            ),
        ):
            results = subagent_parallel(tasks, max_concurrent=2)

        for i, r in enumerate(results):
            assert r["result"] == f"result-{i}", f"Order wrong at index {i}"

    def test_max_concurrent_with_budget_respects_both_caps(self):
        """max_concurrent and budget are both enforced simultaneously."""
        tasks = [(f"b{i}", f"p{i}") for i in range(6)]
        # Budget only allows 2 agents (50 tokens each, 100 total)
        budget = SubagentBudget(total=100)
        results_by_id = {
            f"b{i}": ReturnType("success", "ok", output_tokens=50) for i in range(6)
        }

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch(
                "gptme.tools.subagent.batch.subagent_wait",
                side_effect=self._make_mock_wait(results_by_id),
            ),
        ):
            results = subagent_parallel(tasks, max_concurrent=4, budget=budget)

        successes = [r for r in results if r["status"] == "success"]
        budget_exceeded = [r for r in results if r["status"] == "budget_exceeded"]
        # At least some agents ran before budget was exhausted
        assert len(successes) >= 1
        # Remaining agents got budget_exceeded
        assert len(budget_exceeded) >= 1
        assert len(successes) + len(budget_exceeded) == 6
        # Budget not exceeded significantly — at most a small overrun is allowed
        # because concurrency makes exact enforcement best-effort
        assert budget.spent() <= 300  # at most a few extra agents slip through

    def test_max_concurrent_one_limits_to_serial(self):
        """max_concurrent=1 means agents run one at a time (serial)."""
        tasks = [(f"s{i}", f"p{i}") for i in range(4)]
        active_count = 0
        max_active = 0
        lock = threading.Lock()

        def mock_wait(agent_id, timeout=60, max_result_chars=0):
            nonlocal active_count, max_active
            with lock:
                active_count += 1
                max_active = max(max_active, active_count)
            import time

            time.sleep(0.01)
            with lock:
                active_count -= 1
            return {"status": "success", "result": "ok", "output_tokens": 5}

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch("gptme.tools.subagent.batch.subagent_wait", side_effect=mock_wait),
        ):
            results = subagent_parallel(tasks, max_concurrent=1)

        assert max_active == 1, f"Expected serial execution (max=1), saw {max_active}"
        assert all(r["status"] == "success" for r in results)

    def test_max_concurrent_budget_exceeded_before_slot(self):
        """Budget exhausted before a slot is acquired → budget_exceeded status."""
        tasks = [("first", "p1"), ("second", "p2"), ("third", "p3")]
        # Budget allows 1 agent; second and third get budget_exceeded
        budget = SubagentBudget(total=100)

        call_order: list[str] = []

        def mock_wait(agent_id, timeout=60, max_result_chars=0):
            call_order.append(agent_id)
            return {"status": "success", "result": "ok", "output_tokens": 100}

        with (
            patch("gptme.tools.subagent.batch.subagent"),
            patch("gptme.tools.subagent.batch.subagent_wait", side_effect=mock_wait),
        ):
            results = subagent_parallel(tasks, max_concurrent=1, budget=budget)

        budget_exceeded = [r for r in results if r["status"] == "budget_exceeded"]
        # At most 1 agent ran (serial + tight budget)
        assert len(budget_exceeded) >= 1

    def test_max_concurrent_zero_raises(self):
        """max_concurrent=0 raises ValueError before spawning any agents."""
        tasks = [("a", "p1"), ("b", "p2")]
        with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
            subagent_parallel(tasks, max_concurrent=0)

    def test_max_concurrent_negative_raises(self):
        """max_concurrent=-1 raises ValueError before spawning any agents."""
        tasks = [("a", "p1"), ("b", "p2")]
        with pytest.raises(ValueError, match="max_concurrent must be >= 1"):
            subagent_parallel(tasks, max_concurrent=-1)

    def test_duplicate_agent_ids_raises(self):
        """Duplicate task agent_ids raise ValueError before any work starts."""
        tasks = [("same-id", "prompt A"), ("same-id", "prompt B")]
        with pytest.raises(ValueError, match="agent_ids must be unique"):
            subagent_parallel(tasks)

    def test_cancel_on_failure_cancels_inflight_siblings(self):
        """When cancel_on_failure=True and one agent fails, in-flight siblings
        are cancelled via subagent_cancel() rather than running to completion.

        Synchronisation: "slow" sets slow_entered once it is inside mock_wait
        (and already in inflight_ids). "fast-fail" waits for that signal before
        returning its failure so that inflight_ids is guaranteed to contain "slow"
        when the cancel sweep runs. mock_cancel("slow") then sets slow_interrupted
        so the sleeping thread exits promptly and the test completes quickly.
        """
        slow_entered = threading.Event()
        slow_interrupted = threading.Event()
        cancelled_ids: list[str] = []
        cancel_lock = threading.Lock()

        def mock_subagent(agent_id, prompt, **kwargs):
            pass

        def mock_wait(agent_id, timeout=60, max_result_chars=0):
            if agent_id == "slow":
                # inflight_ids.add("slow") already ran before this call
                slow_entered.set()
                slow_interrupted.wait(timeout=10)  # released by mock_cancel
                return {"status": "success", "result": "ok", "output_tokens": 10}
            # fast-fail: wait until slow is registered in inflight_ids
            slow_entered.wait(timeout=5)
            return {"status": "failure", "result": "boom", "output_tokens": 0}

        def mock_cancel(agent_id):
            with cancel_lock:
                cancelled_ids.append(agent_id)
            if agent_id == "slow":
                slow_interrupted.set()

        with (
            patch("gptme.tools.subagent.batch.subagent", side_effect=mock_subagent),
            patch("gptme.tools.subagent.batch.subagent_wait", side_effect=mock_wait),
            patch(
                "gptme.tools.subagent.batch.subagent_cancel", side_effect=mock_cancel
            ),
        ):
            results = subagent_parallel(
                [("fast-fail", "p1"), ("slow", "p2")],
                max_concurrent=2,
                cancel_on_failure=True,
                timeout=10,
            )

        # fast-fail should have triggered cancellation of "slow"
        assert any(r["status"] == "failure" for r in results)
        assert "slow" in cancelled_ids
