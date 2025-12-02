"""Tests for the STT (Speech-to-Text) tool."""

import pytest


def test_stt_module_imports():
    """Test that the STT module can be imported."""
    from gptme.tools.stt import (
        _is_stt_available,
        record_and_transcribe,
        transcribe_audio,
    )

    # Functions should exist
    assert callable(_is_stt_available)
    assert callable(transcribe_audio)
    assert callable(record_and_transcribe)


def test_stt_tool_spec():
    """Test that the STT tool spec is correctly defined."""
    from gptme.tools.stt import tool

    assert tool.name == "stt"
    assert "Speech-to-text" in tool.desc or "speech" in tool.desc.lower()
    # Tool may or may not be available depending on dependencies
    assert isinstance(tool.available, bool)


def test_transcribe_audio_no_client():
    """Test transcription fails gracefully without OpenAI client."""
    import os

    from gptme.tools.stt import transcribe_audio

    original_key = os.environ.get("OPENAI_API_KEY")
    try:
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]

        # Should return None without crashing
        result = transcribe_audio(b"fake audio data")
        assert result is None
    finally:
        if original_key:
            os.environ["OPENAI_API_KEY"] = original_key


@pytest.mark.skipif(
    True,  # Skip by default as it requires audio hardware
    reason="Requires audio hardware and user interaction",
)
def test_record_and_transcribe():
    """Test the full record and transcribe flow (requires audio hardware)."""
    # This test would require actual audio hardware and user interaction
    # Skip in CI environments
    pass


def test_stt_availability_check():
    """Test STT availability check doesn't crash."""
    from gptme.tools.stt import _is_stt_available

    # Should return bool without crashing
    result = _is_stt_available()
    assert isinstance(result, bool)
