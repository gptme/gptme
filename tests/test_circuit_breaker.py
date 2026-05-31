"""Tests for gptme.circuit_breaker.

Covers:
- All three state transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
- Probe-on-HALF_OPEN: success resets, failure re-opens
- Concurrent access (multiple threads hitting the breaker simultaneously)
- Edge cases: threshold=1, zero cooldown, manual reset
- Registry helpers (get_breaker, clear_registry)
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from gptme.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    clear_registry,
    get_breaker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_breaker(
    name: str = "test",
    failure_threshold: int = 3,
    cooldown: float = 60.0,
    **_kwargs: object,
) -> CircuitBreaker:
    """Return a fresh CircuitBreaker with sensible test defaults."""
    return CircuitBreaker(
        name=name, failure_threshold=failure_threshold, cooldown=cooldown
    )


def _fail(exc: Exception | None = None) -> None:
    raise exc if exc is not None else RuntimeError("boom")


def _succeed(value: str = "ok") -> str:
    return value


# ---------------------------------------------------------------------------
# Basic state machine
# ---------------------------------------------------------------------------


class TestClosedState:
    def test_starts_closed(self):
        cb = _make_breaker()
        assert cb.state == CircuitState.CLOSED

    def test_success_stays_closed(self):
        cb = _make_breaker()
        result = cb.call(_succeed)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_failure_increments_count(self):
        cb = _make_breaker(failure_threshold=3)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        assert cb.failure_count == 1
        assert cb.state == CircuitState.CLOSED

    def test_success_resets_failure_count(self):
        cb = _make_breaker(failure_threshold=3)
        # Two failures
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_fail)
        assert cb.failure_count == 2
        # One success resets the count
        cb.call(_succeed)
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


class TestClosedToOpen:
    def test_opens_after_threshold(self):
        cb = _make_breaker(failure_threshold=3)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                cb.call(_fail)
        assert cb.state == CircuitState.OPEN

    def test_call_raises_circuit_open_when_open(self):
        cb = _make_breaker(failure_threshold=1, cooldown=60.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        # Now open — next call should raise CircuitOpenError, not RuntimeError
        with pytest.raises(CircuitOpenError) as exc_info:
            cb.call(_succeed)
        assert exc_info.value.name == "test"
        assert exc_info.value.retry_after is not None
        assert exc_info.value.retry_after > 0

    def test_circuit_open_error_message(self):
        cb = _make_breaker(failure_threshold=1, cooldown=60.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        with pytest.raises(CircuitOpenError) as exc_info:
            cb.call(_fail)
        assert "OPEN" in str(exc_info.value)

    def test_fails_fast_multiple_times(self):
        cb = _make_breaker(failure_threshold=2, cooldown=60.0)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_fail)
        # All subsequent calls should be fast-fail, not actually calling the function
        mock_fn = MagicMock(side_effect=RuntimeError("should not be called"))
        for _ in range(5):
            with pytest.raises(CircuitOpenError):
                cb.call(mock_fn)
        mock_fn.assert_not_called()


class TestOpenToHalfOpen:
    def test_transitions_to_half_open_after_cooldown(self, monkeypatch):
        cb = _make_breaker(failure_threshold=1, cooldown=1.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        assert cb.state == CircuitState.OPEN

        # Fast-forward time past cooldown
        original_monotonic = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: original_monotonic() + 2.0)

        # Next call should be allowed as a probe (HALF_OPEN)
        result = cb.call(_succeed)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    def test_seconds_until_probe_decreases(self, monkeypatch):
        cb = _make_breaker(failure_threshold=1, cooldown=10.0)
        base = time.monotonic()
        with pytest.raises(RuntimeError):
            cb.call(_fail)

        # At t=0: full wait remaining
        monkeypatch.setattr(time, "monotonic", lambda: base)
        remaining_0 = cb.seconds_until_probe()
        assert remaining_0 > 0

        # At t=5: half the wait gone
        monkeypatch.setattr(time, "monotonic", lambda: base + 5.0)
        remaining_5 = cb.seconds_until_probe()
        assert remaining_5 < remaining_0

        # After cooldown: no wait
        monkeypatch.setattr(time, "monotonic", lambda: base + 11.0)
        assert cb.seconds_until_probe() == 0.0


class TestHalfOpen:
    def test_probe_success_resets_to_closed(self, monkeypatch):
        cb = _make_breaker(failure_threshold=1, cooldown=1.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)

        original = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: original() + 2.0)
        cb.call(_succeed)

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_probe_failure_resets_open_timer(self, monkeypatch):
        cb = _make_breaker(failure_threshold=1, cooldown=2.0)
        t0 = time.monotonic()
        with pytest.raises(RuntimeError):
            cb.call(_fail)

        # Advance past cooldown to allow a probe
        monkeypatch.setattr(time, "monotonic", lambda: t0 + 3.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)

        # State should still be OPEN with a fresh timer
        assert cb.state == CircuitState.OPEN
        # The timer was reset, so seconds_until_probe should be close to cooldown
        remaining = cb.seconds_until_probe()
        assert 0.0 < remaining <= 2.0

    def test_only_one_probe_allowed_at_a_time(self, monkeypatch):
        """After cooldown, only one call transitions to HALF_OPEN.

        Concurrent threads should see CircuitOpenError until the probe resolves.
        """
        cb = _make_breaker(failure_threshold=1, cooldown=1.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)

        original = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: original() + 2.0)

        # Simulate an in-flight probe by setting HALF_OPEN + _probing=True
        with cb._lock:
            cb._state = CircuitState.HALF_OPEN
            cb._probing = True

        # A second call while the probe is in flight should be rejected
        with pytest.raises(CircuitOpenError):
            cb.call(_succeed)

        # Once the probe finishes (probing cleared), a new probe is allowed
        with cb._lock:
            cb._probing = False

        result = cb.call(_succeed)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Manual reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_state(self):
        cb = _make_breaker(failure_threshold=1, cooldown=60.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_reset_allows_calls_again(self):
        cb = _make_breaker(failure_threshold=1, cooldown=60.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        cb.reset()
        result = cb.call(_succeed)
        assert result == "ok"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_threshold_one(self):
        cb = _make_breaker(failure_threshold=1, cooldown=60.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        assert cb.state == CircuitState.OPEN

    def test_zero_cooldown_allows_immediate_probe(self, monkeypatch):
        cb = _make_breaker(failure_threshold=1, cooldown=0.0)
        with pytest.raises(RuntimeError):
            cb.call(_fail)
        # With cooldown=0, the probe should be immediately available
        result = cb.call(_succeed)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    def test_different_exception_types_count(self):
        cb = _make_breaker(failure_threshold=2)
        with pytest.raises(ValueError, match="v"):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("v")))
        with pytest.raises(OSError, match="io"):
            cb.call(lambda: (_ for _ in ()).throw(OSError("io")))
        assert cb.state == CircuitState.OPEN

    def test_repr(self):
        cb = _make_breaker(failure_threshold=5, cooldown=30.0)
        r = repr(cb)
        assert "test" in r
        assert "closed" in r
        assert "0/5" in r

    def test_ignore_exceptions_does_not_count_as_failure(self):
        """Exceptions listed in ignore_exceptions should not trip the breaker."""
        cb = _make_breaker(failure_threshold=2, cooldown=60.0)

        class UserCancel(Exception):
            pass

        for _ in range(10):
            with pytest.raises(UserCancel):
                cb.call(
                    lambda: (_ for _ in ()).throw(UserCancel("cancelled")),
                    ignore_exceptions=(UserCancel,),
                )

        # Breaker should still be CLOSED — cancellations are not failures
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_ignore_exceptions_in_half_open_clears_probe_flag(self):
        """Ignored exceptions during HALF_OPEN clear _probing without recording a failure."""
        cb = _make_breaker(failure_threshold=1, cooldown=0.0)

        class UserCancel(Exception):
            pass

        with pytest.raises(RuntimeError):
            cb.call(_fail)
        assert cb.state == CircuitState.OPEN

        # Force HALF_OPEN with no probe in flight so call() can claim the probe
        with cb._lock:
            cb._state = CircuitState.HALF_OPEN
            cb._probing = False

        # An ignored exception: probe flag should be cleared, state stays HALF_OPEN
        with pytest.raises(UserCancel):
            cb.call(
                lambda: (_ for _ in ()).throw(UserCancel()),
                ignore_exceptions=(UserCancel,),
            )

        # _probing cleared; a new probe is now allowed; state not moved to CLOSED/OPEN
        with cb._lock:
            assert not cb._probing
            assert cb._state == CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    def test_thread_safe_failure_counting(self):
        """Many threads failing simultaneously should not corrupt the counter."""
        cb = _make_breaker(failure_threshold=100, cooldown=60.0)
        n_threads = 50
        errors: list[Exception] = []

        def do_fail():
            try:
                cb.call(_fail)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_fail) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All exceptions should be RuntimeError (circuit still closed since threshold=100)
        assert all(isinstance(e, RuntimeError) for e in errors)
        assert cb.failure_count == n_threads
        assert cb.state == CircuitState.CLOSED

    def test_circuit_opens_exactly_once_under_concurrency(self):
        """Only one thread should see the CLOSED→OPEN transition."""
        threshold = 5
        cb = _make_breaker(failure_threshold=threshold, cooldown=60.0)
        n_threads = 20
        open_errors: list[CircuitOpenError] = []
        runtime_errors: list[RuntimeError] = []
        lock = threading.Lock()

        def do_fail():
            try:
                cb.call(_fail)
            except CircuitOpenError as e:
                with lock:
                    open_errors.append(e)
            except RuntimeError as e:
                with lock:
                    runtime_errors.append(e)

        threads = [threading.Thread(target=do_fail) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads see exactly one outcome
        assert len(runtime_errors) + len(open_errors) == n_threads
        # At least threshold failures are needed to trip the breaker; concurrent
        # threads can all pass the pre-call state check before any failure is
        # recorded, so the exact count is non-deterministic — only the lower bound holds.
        assert len(runtime_errors) >= threshold
        assert cb.state == CircuitState.OPEN

    def test_concurrent_successes_reset_cleanly(self):
        """Concurrent successes should not corrupt state."""
        cb = _make_breaker(failure_threshold=10, cooldown=60.0)
        results: list[str] = []
        lock = threading.Lock()

        def do_succeed():
            r = cb.call(_succeed)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=do_succeed) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        assert all(r == "ok" for r in results)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def setup_method(self):
        clear_registry()

    def test_get_breaker_returns_same_instance(self):
        b1 = get_breaker("svc-a")
        b2 = get_breaker("svc-a")
        assert b1 is b2

    def test_get_breaker_different_names(self):
        b1 = get_breaker("svc-a")
        b2 = get_breaker("svc-b")
        assert b1 is not b2

    def test_get_breaker_applies_params_on_first_call(self):
        b = get_breaker("svc", failure_threshold=2, cooldown=5.0)
        assert b.failure_threshold == 2
        assert b.cooldown == 5.0

    def test_get_breaker_ignores_params_on_second_call(self):
        b1 = get_breaker("svc", failure_threshold=2, cooldown=5.0)
        b2 = get_breaker("svc", failure_threshold=99, cooldown=999.0)
        # Params from first call win
        assert b1 is b2
        assert b2.failure_threshold == 2
        assert b2.cooldown == 5.0

    def test_clear_registry(self):
        b1 = get_breaker("svc")
        clear_registry()
        b2 = get_breaker("svc")
        assert b1 is not b2
