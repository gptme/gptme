"""Check cleanup when the system audio backend cannot write its WAV."""

import queue
import tempfile
from pathlib import Path
from typing import Any

import pytest

from gptme.util import sound


def test_failed_wav_write_cleans_up_before_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    np = pytest.importorskip("numpy")
    wavfile = pytest.importorskip("scipy.io.wavfile")
    tasks: queue.Queue[Any] = queue.Queue()
    data = np.array([0.5], dtype=np.float32)
    tasks.put((data, 44100))
    tasks.put(None)
    monkeypatch.setattr(sound, "audio_queue", tasks)
    monkeypatch.setattr(sound, "is_cmd_audio_available", lambda: True)
    monkeypatch.setattr(sound, "is_sounddevice_available", lambda: True)
    monkeypatch.setattr(sound, "convert_audio_to_float32", lambda samples: samples)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def fail_write(path: Path, rate: int, samples: Any) -> None:
        path.write_bytes(b"RIFF")
        raise OSError("simulated failure during WAV write")

    fallback_files: list[list[Path]] = []

    def fallback(samples: Any, rate: int, volume: float) -> None:
        fallback_files.append(list(tmp_path.glob("*.wav")))

    monkeypatch.setattr(wavfile, "write", fail_write)
    monkeypatch.setattr(sound, "play_with_sounddevice", fallback)
    sound._audio_player_thread_fn()

    assert fallback_files == [[]]
    assert list(tmp_path.iterdir()) == []
