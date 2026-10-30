"""In-process readiness signals for existing asynchronous tool inboxes.

Signals carry no payload and never consume an inbox. Subscribers register before
checking the queues; a callback only schedules work on its surface's event loop.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_listeners: dict[Path, list[Callable[[], None]]] = {}


def subscribe(logdir: Path, callback: Callable[[], None]) -> Callable[[], None]:
    """Subscribe to one conversation; return an idempotent unsubscribe function."""
    parent = logdir.resolve()
    with _lock:
        _listeners.setdefault(parent, []).append(callback)

    def unsubscribe() -> None:
        with _lock:
            listeners = _listeners.get(parent, [])
            if callback in listeners:
                listeners.remove(callback)
            if not listeners:
                _listeners.pop(parent, None)

    return unsubscribe


def signal(logdir: Path | None) -> None:
    """Signal after enqueueing, outside inbox locks; callbacks may race teardown."""
    if logdir is None:
        return
    with _lock:
        listeners = tuple(_listeners.get(logdir.resolve(), ()))
    for callback in listeners:
        try:
            callback()
        except Exception:
            logger.debug("Notification readiness callback failed", exc_info=True)
