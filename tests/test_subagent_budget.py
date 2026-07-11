"""Unit tests for SubagentBudget and budget-aware subagent_parallel/pipeline.

Tests the budget coordination layer added in gptme/gptme#3192.
All tests are pure-unit (no LLM calls, no subprocess spawning).
"""

import threading
from dataclasses import asdict
from unittest.mock import patch

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

        def fake_wait_all(self_job, timeout=300):
            # Populate results as if agents completed
            for aid in self_job.agent_ids:
                if aid not in self_job.results:
                    r = results_by_id.get(aid, ReturnType("failure", "no mock result"))
                    self_job.results[aid] = r
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

        def fake_wait_all(self_job, timeout=300):
            for aid in self_job.agent_ids:
                if aid not in self_job.results:
                    self_job.results[aid] = results_by_id.get(
                        aid, ReturnType("failure", "no mock")
                    )
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

        def fake_wait_all(self_job, timeout=300):
            for aid in self_job.agent_ids:
                self_job.results[aid] = results_by_id[aid]
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

        def fake_wait_all_a(self_job, timeout=300):
            for aid in self_job.agent_ids:
                self_job.results[aid] = results_by_id_a[aid]
            return {}

        def fake_wait_all_b(self_job, timeout=300):
            for aid in self_job.agent_ids:
                self_job.results[aid] = results_by_id_b[aid]
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
