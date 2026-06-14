"""Unit tests for subagent concurrency limiter."""

import threading
import time

import pytest

from gptme.tools.subagent.concurrency import (
    _max_concurrent,
    _reset_slot_sem,
    get_slot_sem,
)


@pytest.fixture(autouse=True)
def reset_semaphore():
    """Reset the global semaphore before and after each test."""
    _reset_slot_sem()
    yield
    _reset_slot_sem()


class TestMaxConcurrent:
    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("GPTME_SUBAGENT_MAX_CONCURRENT", "3")
        assert _max_concurrent() == 3

    def test_env_var_overrides_config(self, monkeypatch):
        monkeypatch.setenv("GPTME_SUBAGENT_MAX_CONCURRENT", "2")
        # Even if config would return something else, env wins
        assert _max_concurrent() == 2

    def test_default_is_reasonable(self, monkeypatch):
        monkeypatch.delenv("GPTME_SUBAGENT_MAX_CONCURRENT", raising=False)
        val = _max_concurrent()
        assert 1 <= val <= 16, f"default {val} not in reasonable range [1, 16]"

    def test_env_var_is_integer(self, monkeypatch):
        monkeypatch.setenv("GPTME_SUBAGENT_MAX_CONCURRENT", "7")
        assert _max_concurrent() == 7

    def test_env_var_invalid_string_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("GPTME_SUBAGENT_MAX_CONCURRENT", "not-a-number")
        val = _max_concurrent()
        assert 1 <= val <= 16, f"fallback {val} not in reasonable range"

    def test_env_var_zero_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("GPTME_SUBAGENT_MAX_CONCURRENT", "0")
        val = _max_concurrent()
        assert val >= 1, "zero env var must not produce a deadlocking semaphore"

    def test_env_var_negative_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("GPTME_SUBAGENT_MAX_CONCURRENT", "-1")
        val = _max_concurrent()
        assert val >= 1, "negative env var must not produce a deadlocking semaphore"


class TestGetSlotSem:
    def test_returns_bounded_semaphore(self):
        sem = get_slot_sem()
        assert isinstance(sem, type(threading.BoundedSemaphore()))

    def test_same_instance_on_repeated_calls(self):
        sem1 = get_slot_sem()
        sem2 = get_slot_sem()
        assert sem1 is sem2

    def test_reset_creates_new_instance(self):
        sem1 = get_slot_sem()
        _reset_slot_sem()
        sem2 = get_slot_sem()
        assert sem1 is not sem2

    def test_reset_with_explicit_count(self, monkeypatch):
        monkeypatch.delenv("GPTME_SUBAGENT_MAX_CONCURRENT", raising=False)
        _reset_slot_sem(max_concurrent=4)
        sem = get_slot_sem()
        # Acquire 4 times — 5th should block (non-blocking acquire returns False)
        acquired = [sem.acquire(blocking=False) for _ in range(4)]
        assert all(acquired), "should acquire 4 times with cap=4"
        assert not sem.acquire(blocking=False), "5th acquire should fail with cap=4"
        # Release all
        for _ in range(4):
            sem.release()


class TestConcurrencyEnforcement:
    """Verify the semaphore actually caps concurrent executions."""

    def test_cap_enforced_with_mock_workers(self, monkeypatch):
        """N workers with cap=3: peak concurrency must not exceed 3."""
        CAP = 3
        N = 10
        _reset_slot_sem(max_concurrent=CAP)
        sem = get_slot_sem()

        peak = [0]
        current = [0]
        lock = threading.Lock()

        def worker():
            sem.acquire()
            try:
                with lock:
                    current[0] += 1
                    if current[0] > peak[0]:
                        peak[0] = current[0]
                time.sleep(0.01)  # simulate work
                with lock:
                    current[0] -= 1
            finally:
                sem.release()

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert peak[0] <= CAP, f"peak concurrency {peak[0]} exceeded cap {CAP}"
