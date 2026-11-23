"""Audio worker queue accounting without accessing an audio device."""

import queue
import threading
from typing import Any

import pytest

from gptme.util import sound


class ObservedQueue(queue.Queue[Any]):
    def __init__(self) -> None:
        super().__init__()
        self.waiting = threading.Event()

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        if self.empty():
            self.waiting.set()
        return super().get(block, timeout)


class Samples:
    shape = (1,)

    def __mul__(self, volume: float) -> "Samples":
        return self


@pytest.mark.parametrize("fail", [False, True])
def test_worker_completes_dequeued_items(
    monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    tasks = ObservedQueue()
    tasks.put((Samples(), 44100))
    monkeypatch.setattr(sound, "audio_queue", tasks)

    def available() -> bool:
        if fail:
            raise RuntimeError("audio backend unavailable")
        return False

    monkeypatch.setattr(sound, "is_cmd_audio_available", available)
    monkeypatch.setattr(sound, "is_sounddevice_available", lambda: False)
    worker = threading.Thread(target=sound._audio_player_thread_fn, daemon=True)
    worker.start()
    try:
        assert tasks.waiting.wait(2), "worker did not finish processing"
        assert tasks.unfinished_tasks == 0
    finally:
        tasks.put(None)
        worker.join(2)
    assert not worker.is_alive()
    assert tasks.unfinished_tasks == 0, "shutdown sentinel must also be completed"
