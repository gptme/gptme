"""Tests for gptme.k_best — K-Best Guess validation framework."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from gptme.k_best import Candidate, KBestResult, k_best_guess

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def always_returns(value: Any):
    """Return a function that always returns *value*."""

    def fn(*_args, **_kwargs):
        return value

    return fn


def counter_fn(counter: list[int]):
    """Increment counter[0] and return its current value."""

    def fn(*_args, **_kwargs):
        counter[0] += 1
        return counter[0]

    return fn


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


class TestKBestGuessBasic:
    def test_k1_returns_single_result(self):
        @k_best_guess(k=1)
        def fn():
            return 42

        assert fn() == 42

    def test_default_no_check_returns_first_successful(self):
        """Without a check function, any successful candidate is fine."""
        calls: list[int] = []

        @k_best_guess(k=3)
        def fn():
            calls.append(1)
            return "ok"

        result = fn()
        assert result == "ok"
        assert len(calls) == 3  # all K candidates ran

    def test_check_fn_selects_best(self):
        """check= should pick the highest-scoring candidate."""
        outcomes = iter([1, 5, 3])

        @k_best_guess(k=3, check=lambda x: x, parallel=False)
        def fn():
            return next(outcomes)

        result = fn()
        assert result == 5

    def test_check_fn_float_scores(self):
        outcomes = iter([0.1, 0.9, 0.5])

        @k_best_guess(k=3, check=lambda x: x, parallel=False)
        def fn():
            return next(outcomes)

        assert fn() == pytest.approx(0.9)

    def test_pass_through_args(self):
        @k_best_guess(k=2, check=lambda x: x)
        def fn(a: int, b: int) -> int:
            return a + b

        assert fn(3, 4) == 7

    def test_pass_through_kwargs(self):
        @k_best_guess(k=2)
        def fn(*, msg: str) -> str:
            return msg.upper()

        assert fn(msg="hello") == "HELLO"

    def test_wraps_preserves_name(self):
        @k_best_guess(k=2)
        def my_special_function():
            return None

        assert my_special_function.__name__ == "my_special_function"

    def test_k_must_be_positive(self):
        with pytest.raises(ValueError, match="k must be >= 1"):
            k_best_guess(k=0)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestKBestGuessErrors:
    def test_all_fail_reraises_last(self):
        @k_best_guess(k=3, parallel=False)
        def always_explodes():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            always_explodes()

    def test_partial_failure_still_returns_winner(self):
        call_count = [0]

        @k_best_guess(k=3, check=lambda x: x, parallel=False)
        def flaky():
            call_count[0] += 1
            n = call_count[0]
            if n == 1:
                raise ValueError("first fails")
            return n  # 2 or 3; 3 wins

        result = flaky()
        assert result == 3

    def test_check_raises_treated_as_minus_inf(self):
        """If check() itself raises, that candidate should still lose."""
        outcomes = iter([10, 20])

        def bad_check(x):
            if x == 10:
                raise TypeError("bad!")
            return x

        @k_best_guess(k=2, check=bad_check, parallel=False)
        def fn():
            return next(outcomes)

        assert fn() == 20

    def test_all_checks_fail_reraises_last_validation_error(self):
        def invalid_check(_result):
            raise ValueError("invalid")

        @k_best_guess(k=3, check=invalid_check, parallel=False)
        def fn():
            return "unvalidated"

        with pytest.raises(ValueError, match="invalid"):
            fn()

    @pytest.mark.parametrize(
        "non_finite_score", [float("nan"), float("inf"), float("-inf")]
    )
    def test_non_finite_score_is_rejected(self, non_finite_score):
        outcomes = iter(["invalid", "valid"])

        @k_best_guess(
            k=2,
            check=lambda result: non_finite_score if result == "invalid" else 1.0,
            parallel=False,
            return_metadata=True,
        )
        def fn():
            return next(outcomes)

        result = fn()
        assert isinstance(result, KBestResult)
        assert result.winner == "valid"
        assert isinstance(result.candidates[0].error, ValueError)


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------


class TestKBestGuessParallel:
    def test_parallel_all_called(self):
        lock = threading.Lock()
        calls: list[int] = []

        @k_best_guess(k=4, parallel=True)
        def fn():
            with lock:
                calls.append(1)
            return "x"

        fn()
        assert len(calls) == 4

    def test_parallel_check_fn_selects_best(self):
        # All threads return the same value; score it by identity
        @k_best_guess(k=5, check=lambda x: x, parallel=True)
        def fn():
            return 7

        assert fn() == 7

    def test_parallel_partial_failure(self):
        """Some threads raise; winner should still be returned."""
        call_count = [0]
        lock = threading.Lock()

        @k_best_guess(k=4, check=lambda x: x, parallel=True)
        def fn():
            with lock:
                call_count[0] += 1
                n = call_count[0]
            if n % 2 == 0:
                raise RuntimeError("even fails")
            return n  # 1 or 3

        result = fn()
        assert result in (1, 3)


# ---------------------------------------------------------------------------
# return_metadata
# ---------------------------------------------------------------------------


class TestKBestGuessMetadata:
    def test_returns_kbest_result(self):
        @k_best_guess(k=3, return_metadata=True)
        def fn():
            return 42

        out = fn()
        assert isinstance(out, KBestResult)
        assert out.winner == 42
        assert out.k == 3

    def test_metadata_winner_matches_best(self):
        outcomes = iter([1, 9, 4])

        @k_best_guess(k=3, check=lambda x: x, parallel=False, return_metadata=True)
        def fn():
            return next(outcomes)

        out = fn()
        assert isinstance(out, KBestResult)
        assert out.winner == 9
        assert out.winner_score == pytest.approx(9.0)

    def test_metadata_includes_all_candidates(self):
        @k_best_guess(k=3, return_metadata=True)
        def fn():
            return "x"

        out = fn()
        assert isinstance(out, KBestResult)
        assert len(out.candidates) == 3
        for c in out.candidates:
            assert isinstance(c, Candidate)

    def test_metadata_records_errors(self):
        call_count = [0]

        @k_best_guess(k=3, parallel=False, return_metadata=True)
        def fn():
            call_count[0] += 1
            if call_count[0] == 2:
                raise ValueError("second fails")
            return "ok"

        out = fn()
        assert isinstance(out, KBestResult)
        errors = [c for c in out.candidates if c.error is not None]
        assert len(errors) == 1
        assert isinstance(errors[0].error, ValueError)
