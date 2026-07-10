from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip


def _set_openrouter_key(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr(
        "gptme.config.get_config",
        lambda: SimpleNamespace(get_env=lambda key, default=None: value),
    )


def test_tts_endpoint_rejects_non_string_text(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    _set_openrouter_key(monkeypatch, "test-key")

    response = client.post("/api/v2/audio/speech", json={"text": 1})

    assert response.status_code == 400
    assert response.get_json() == {"error": "text must be a string"}


@pytest.mark.parametrize("field", ["model", "voice"])
def test_tts_endpoint_rejects_non_string_optional_fields(
    field: str, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    _set_openrouter_key(monkeypatch, "test-key")

    response = client.post(
        "/api/v2/audio/speech", json={"text": "Hello", field: ["bad"]}
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": f"{field} must be a string"}


def test_tts_endpoint_maps_provider_errors_to_bad_gateway(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    _set_openrouter_key(monkeypatch, "test-key")
    monkeypatch.setattr(
        "gptme.server.tts_api.requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            status_code=400,
            text="invalid voice",
        ),
    )

    response = client.post("/api/v2/audio/speech", json={"text": "Hello"})

    assert response.status_code == 502
    assert response.get_json() == {"error": "TTS provider error: 400"}


# --- kokoro-onnx fallback tests ---


def test_tts_no_key_no_kokoro_returns_503(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """When OPENROUTER_API_KEY is absent and kokoro-onnx is not installed → 503."""
    _set_openrouter_key(monkeypatch, None)
    monkeypatch.setattr("gptme.server.tts_api._KokoroClass", None)
    monkeypatch.setattr("gptme.server.tts_api._kokoro_instance", None)

    response = client.post("/api/v2/audio/speech", json={"text": "Hello"})

    assert response.status_code == 503
    body = response.get_json()
    assert "kokoro-onnx" in body["error"]
    assert "OPENROUTER_API_KEY" in body["error"]


def test_tts_no_key_kokoro_installed_but_models_missing_returns_503(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """kokoro-onnx installed but model files absent → 503 with download hint."""
    _set_openrouter_key(monkeypatch, None)

    fake_class = MagicMock()
    monkeypatch.setattr("gptme.server.tts_api._KokoroClass", fake_class)
    monkeypatch.setattr("gptme.server.tts_api._kokoro_instance", None)
    # Point model paths at non-existent files inside tmp_path
    monkeypatch.setattr(
        "gptme.server.tts_api.KOKORO_MODEL_PATH", str(tmp_path / "missing.onnx")
    )
    monkeypatch.setattr(
        "gptme.server.tts_api.KOKORO_VOICES_PATH", str(tmp_path / "missing.bin")
    )

    response = client.post("/api/v2/audio/speech", json={"text": "Hello"})

    assert response.status_code == 503
    body = response.get_json()
    assert "model files not found" in body["error"]


def test_tts_no_key_kokoro_returns_wav(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """When kokoro is available and synthesises successfully → 200 WAV."""
    import io

    import numpy as np

    _set_openrouter_key(monkeypatch, None)

    # Build a minimal WAV via soundfile (skip if soundfile not installed)
    sf = pytest.importorskip("soundfile", reason="soundfile not installed")

    samples = np.zeros(100, dtype=np.float32)
    sample_rate = 22050

    fake_kokoro = MagicMock()
    fake_kokoro.create.return_value = (samples, sample_rate)
    fake_kokoro.get_voices.return_value = ["af_heart", "af_bella"]
    monkeypatch.setattr("gptme.server.tts_api._kokoro_instance", fake_kokoro)
    monkeypatch.setattr("gptme.server.tts_api._KokoroClass", MagicMock())

    response = client.post("/api/v2/audio/speech", json={"text": "Hello"})

    assert response.status_code == 200
    assert response.content_type == "audio/wav"
    assert response.headers.get("X-TTS-Backend") == "kokoro-onnx"
    fake_kokoro.create.assert_called_once_with(
        "Hello", voice="af_heart", speed=1.0, lang="en-us"
    )
    # Response body must be a parseable WAV
    buf = io.BytesIO(response.data)
    data, sr = sf.read(buf)
    assert sr == sample_rate


def test_tts_no_key_kokoro_uses_requested_voice_when_available(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """A requested voice present in get_voices() is passed through to kokoro."""
    import numpy as np

    _set_openrouter_key(monkeypatch, None)
    pytest.importorskip("soundfile", reason="soundfile not installed")

    samples = np.zeros(100, dtype=np.float32)
    sample_rate = 22050

    fake_kokoro = MagicMock()
    fake_kokoro.create.return_value = (samples, sample_rate)
    fake_kokoro.get_voices.return_value = ["af_heart", "my_voice"]
    monkeypatch.setattr("gptme.server.tts_api._kokoro_instance", fake_kokoro)
    monkeypatch.setattr("gptme.server.tts_api._KokoroClass", MagicMock())

    response = client.post(
        "/api/v2/audio/speech", json={"text": "Hello", "voice": "my_voice"}
    )

    assert response.status_code == 200
    fake_kokoro.create.assert_called_once_with(
        "Hello", voice="my_voice", speed=1.0, lang="en-us"
    )


def test_tts_no_key_kokoro_unavailable_voice_falls_back_to_default(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """A requested voice absent from get_voices() falls back to af_heart."""
    import numpy as np

    _set_openrouter_key(monkeypatch, None)
    pytest.importorskip("soundfile", reason="soundfile not installed")

    samples = np.zeros(100, dtype=np.float32)
    sample_rate = 22050

    fake_kokoro = MagicMock()
    fake_kokoro.create.return_value = (samples, sample_rate)
    fake_kokoro.get_voices.return_value = ["af_heart", "af_bella"]
    monkeypatch.setattr("gptme.server.tts_api._kokoro_instance", fake_kokoro)
    monkeypatch.setattr("gptme.server.tts_api._KokoroClass", MagicMock())

    response = client.post(
        "/api/v2/audio/speech", json={"text": "Hello", "voice": "nonexistent_voice"}
    )

    assert response.status_code == 200
    fake_kokoro.create.assert_called_once_with(
        "Hello", voice="af_heart", speed=1.0, lang="en-us"
    )


def test_tts_with_key_uses_openrouter_not_kokoro(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """When OPENROUTER_API_KEY is set, kokoro is NOT called even if available."""
    _set_openrouter_key(monkeypatch, "test-key")

    fake_kokoro = MagicMock()
    monkeypatch.setattr("gptme.server.tts_api._kokoro_instance", fake_kokoro)
    monkeypatch.setattr(
        "gptme.server.tts_api.requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            content=b"RIFF....WAVEfmt ",
            status_code=200,
        ),
    )

    response = client.post("/api/v2/audio/speech", json={"text": "Hello"})

    fake_kokoro.create.assert_not_called()
    assert response.status_code == 200
