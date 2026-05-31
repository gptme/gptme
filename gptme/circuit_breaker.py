"""Circuit breaker for flaky tool/MCP calls.

Implements the classic CLOSED → OPEN → HALF_OPEN state machine per tool or
per server name.  After ``failure_threshold`` consecutive failures the breaker
OPEN-circuits and subsequent calls fail immediately without hitting the server.
After ``cooldown`` seconds a single *probe* call is allowed (HALF_OPEN); if it
succeeds the breaker resets to CLOSED, otherwise the open timer is reset.

Thread-safety is provided by a per-breaker ``threading.Lock``.

Usage::

    from gptme.circuit_breaker import CircuitBreaker, CircuitOpenError

    cb = CircuitBreaker(name="my_tool", failure_threshold=5, cooldown=30.0)

    try:
        result = cb.call(my_function, arg1, kwarg=value)
    except CircuitOpenError:
        # tool is currently tripped; handle gracefully
        ...
    except Exception as exc:
        # real failure from the underlying call; breaker recorded it
        ...
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

F = TypeVar("F")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit breaker is open."""

    def __init__(self, name: str, retry_after: float | None = None) -> None:
        self.name = name
        self.retry_after = retry_after
        msg = f"Circuit breaker '{name}' is OPEN"
        if retry_after is not None:
            msg += f"; retry after {retry_after:.1f}s"
        super().__init__(msg)


class CircuitBreaker:
    """Per-resource circuit breaker with CLOSED/OPEN/HALF_OPEN states.

    Args:
        name: Human-readable identifier for logging and error messages.
        failure_threshold: Number of consecutive failures before opening.
            Defaults to 5.
        cooldown: Seconds to wait after opening before allowing a probe.
            Defaults to 30.0.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown

        self._lock = threading.Lock()
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._opened_at: float | None = (
            None  # monotonic timestamp of last OPEN transition
        )

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current state (thread-safe snapshot)."""
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        """Current consecutive failure count (thread-safe snapshot)."""
        with self._lock:
            return self._failure_count

    # ------------------------------------------------------------------
    # Core call interface
    # ------------------------------------------------------------------

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call *func* with circuit-breaker protection.

        Raises:
            CircuitOpenError: If the breaker is OPEN and the cooldown has not
                elapsed yet.
            Exception: Any exception raised by *func* (recorded as a failure).
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0.0)
                remaining = self.cooldown - elapsed
                if remaining > 0:
                    raise CircuitOpenError(self.name, retry_after=remaining)
                # Cooldown elapsed — allow one probe
                logger.debug(
                    "CircuitBreaker '%s': cooldown elapsed, transitioning to HALF_OPEN",
                    self.name,
                )
                self._state = CircuitState.HALF_OPEN

            # CLOSED or HALF_OPEN: let the call through
            # Release the lock while executing so we don't block other threads
            # (they'll see HALF_OPEN and be rejected or wait).
            current_state = self._state

        # Execute outside the lock
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self._record_failure(current_state, exc)
            raise

        self._record_success(current_state)
        return result

    # ------------------------------------------------------------------
    # Internal state transitions
    # ------------------------------------------------------------------

    def _record_success(self, previous_state: CircuitState) -> None:
        with self._lock:
            if previous_state == CircuitState.HALF_OPEN:
                logger.info(
                    "CircuitBreaker '%s': probe succeeded, resetting to CLOSED",
                    self.name,
                )
            else:
                logger.debug(
                    "CircuitBreaker '%s': call succeeded, resetting counters", self.name
                )
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = None

    def _record_failure(self, previous_state: CircuitState, exc: Exception) -> None:
        with self._lock:
            if previous_state == CircuitState.HALF_OPEN:
                # Probe failed — reset the open timer so we wait another cooldown
                logger.warning(
                    "CircuitBreaker '%s': probe FAILED (%s), resetting open timer",
                    self.name,
                    exc,
                )
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                return

            # CLOSED state — increment counter
            self._failure_count += 1
            logger.debug(
                "CircuitBreaker '%s': failure %d/%d (%s)",
                self.name,
                self._failure_count,
                self.failure_threshold,
                exc,
            )
            if self._failure_count >= self.failure_threshold:
                logger.warning(
                    "CircuitBreaker '%s': threshold reached (%d), transitioning to OPEN",
                    self.name,
                    self._failure_count,
                )
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    # ------------------------------------------------------------------
    # Convenience / introspection
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED (useful for testing)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = None

    def seconds_until_probe(self) -> float:
        """Return seconds remaining before a probe is allowed, or 0 if now."""
        with self._lock:
            if self._state != CircuitState.OPEN or self._opened_at is None:
                return 0.0
            elapsed = time.monotonic() - self._opened_at
            return max(0.0, self.cooldown - elapsed)

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"CircuitBreaker(name={self.name!r}, state={self._state.value}, "
                f"failures={self._failure_count}/{self.failure_threshold})"
            )


# ---------------------------------------------------------------------------
# Registry: per-name singleton breakers shared across threads
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
_registry: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    failure_threshold: int = 5,
    cooldown: float = 30.0,
) -> CircuitBreaker:
    """Return the (cached) CircuitBreaker for *name*, creating it if needed.

    Parameters are only applied on first creation; subsequent calls return the
    existing breaker unchanged.
    """
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                cooldown=cooldown,
            )
        return _registry[name]


def clear_registry() -> None:
    """Remove all registered breakers.  Primarily for test isolation."""
    with _registry_lock:
        _registry.clear()


__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "get_breaker",
    "clear_registry",
]
