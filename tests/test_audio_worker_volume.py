"""Verify sample amplitudes at the playback boundary without audio hardware."""

import queue
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gptme.util import _sound_sounddevice, sound

np = pytest.importorskip("numpy")
wavfile = pytest.importorskip("scipy.io.wavfile")


@pytest.mark.parametrize("volume", [0.0, 0.5, 1.0])
@pytest.mark.parametrize("backend", ["sounddevice", "command"])
def test_worker_applies_volume_once(
    monkeypatch: pytest.MonkeyPatch, volume: float, backend: str
) -> None:
    samples = np.array([0.5, -0.5, 0.25], dtype=np.float32)
    original = samples.copy()
    played: list[Any] = []
    tasks: queue.Queue[Any] = queue.Queue()
    tasks.put((samples, 44100))
    tasks.put(None)
    monkeypatch.setattr(sound, "audio_queue", tasks)
    monkeypatch.setattr(sound, "current_volume", volume)
    monkeypatch.setattr(sound, "is_cmd_audio_available", lambda: backend == "command")
    monkeypatch.setattr(sound, "is_sounddevice_available", lambda: True)

    def play(data: Any, rate: int, *, device: int) -> None:
        assert rate == 44100
        played.append(data.copy())

    sd = SimpleNamespace(
        play=play,
        get_stream=lambda: SimpleNamespace(active=False),
        stop=lambda: None,
    )
    monkeypatch.setattr(
        _sound_sounddevice, "_load_audio_libs", lambda: {"np": np, "sd": sd}
    )
    monkeypatch.setattr(_sound_sounddevice, "get_output_device", lambda: (0, 44100))

    def command(path: Path, requested_volume: float) -> bool:
        rate, data = wavfile.read(path)
        assert rate == 44100
        # Model only the external player's requested gain; read the actual WAV
        # produced by the worker rather than replacing its serialization.
        played.append(data.astype(np.float32) / 32767 * requested_volume)
        return True

    monkeypatch.setattr(sound, "play_with_system_command_blocking", command)
    sound._audio_player_thread_fn()
    assert len(played) == 1
    np.testing.assert_allclose(played[0], original * volume, atol=1 / 32767)
    np.testing.assert_array_equal(samples, original)
