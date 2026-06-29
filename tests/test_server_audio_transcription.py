"""Tests for the audio transcription endpoint's untested branches.

The bad-input cases (no file / empty / unsupported format / bad language) are
covered in ``test_server_fresh_bad_input.py``. This file covers the two
remaining untested branches:

- the 413 oversize guard (``_MAX_TRANSCRIPTION_AUDIO_BYTES``), and
- the ``_guess_audio_format`` MIME/filename inference helper.

Both run fully offline (no OpenRouter call).
"""

import io

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server import api_v2


class TestGuessAudioFormat:
    """Unit tests for ``_guess_audio_format`` (pure, no request)."""

    @pytest.mark.parametrize(
        ("mimetype", "expected"),
        [
            ("audio/wav", "wav"),
            ("audio/x-wav", "wav"),
            ("audio/mpeg", "mp3"),
            ("audio/mp3", "mp3"),
            ("audio/mp4", "m4a"),
            ("audio/m4a", "m4a"),
            ("audio/ogg", "ogg"),
            ("audio/webm", "webm"),
            ("audio/flac", "flac"),
            ("audio/aac", "aac"),
        ],
    )
    def test_mimetype_mapping(self, mimetype, expected):
        assert api_v2._guess_audio_format(None, mimetype) == expected

    def test_mimetype_with_codecs_param(self):
        # Browser MediaRecorder sends e.g. "audio/webm;codecs=opus".
        assert api_v2._guess_audio_format(None, "audio/webm;codecs=opus") == "webm"
        assert api_v2._guess_audio_format(None, "audio/ogg; codecs=opus") == "ogg"

    def test_mimetype_is_case_insensitive(self):
        assert api_v2._guess_audio_format(None, "AUDIO/WAV") == "wav"

    def test_filename_fallback_when_mimetype_unknown(self):
        # Generic/unknown MIME falls back to the filename extension.
        assert (
            api_v2._guess_audio_format("recording.mp3", "application/octet-stream")
            == "mp3"
        )

    def test_filename_only(self):
        assert api_v2._guess_audio_format("clip.flac", None) == "flac"

    def test_filename_extension_is_case_insensitive(self):
        assert api_v2._guess_audio_format("CLIP.OGG", None) == "ogg"

    def test_unknown_returns_none(self):
        assert api_v2._guess_audio_format("document.pdf", "application/pdf") is None
        assert api_v2._guess_audio_format(None, None) is None

    def test_every_mapped_value_is_supported(self):
        # Guard: a MIME mapping must never yield an unsupported format string,
        # otherwise the endpoint would 400 on a format it claims to guess.
        for mime in (
            "audio/aac",
            "audio/flac",
            "audio/m4a",
            "audio/mp3",
            "audio/mp4",
            "audio/mpeg",
            "audio/ogg",
            "audio/wav",
            "audio/webm",
            "audio/x-wav",
        ):
            guessed = api_v2._guess_audio_format(None, mime)
            assert guessed in api_v2._SUPPORTED_TRANSCRIPTION_FORMATS


class TestAudioTranscriptionOversize:
    """The 413 oversize guard."""

    def test_oversize_audio_returns_413(self, client, monkeypatch):
        # Shrink the limit instead of uploading 25MB so the test stays fast.
        monkeypatch.setattr(api_v2, "_MAX_TRANSCRIPTION_AUDIO_BYTES", 16)
        data = {"file": (io.BytesIO(b"\x00" * 64), "test.wav")}
        resp = client.post(
            "/api/v2/audio/transcriptions",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 413, (
            f"Expected 413 for oversize audio, got {resp.status_code}: {resp.get_json()}"
        )
        assert "limit" in resp.get_json().get("error", "").lower()
