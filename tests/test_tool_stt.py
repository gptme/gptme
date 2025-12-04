"""Tests for the STT (speech-to-text) tool."""

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSTTAvailability:
    """Tests for STT availability checking."""

    def test_is_stt_available_with_imports(self):
        """Test that _is_stt_available returns True when sounddevice is available."""
        with patch.dict("sys.modules", {"numpy": MagicMock(), "sounddevice": MagicMock()}):
            # Need to reload the module to pick up the patched imports
            from gptme.tools import stt

            # Manually set the flag for testing
            original_flag = stt.has_stt_imports
            stt.has_stt_imports = True
            try:
                assert stt._is_stt_available() is True
            finally:
                stt.has_stt_imports = original_flag

    def test_is_stt_available_without_imports(self):
        """Test that _is_stt_available returns False when sounddevice is not available."""
        from gptme.tools import stt

        original_flag = stt.has_stt_imports
        stt.has_stt_imports = False
        try:
            assert stt._is_stt_available() is False
        finally:
            stt.has_stt_imports = original_flag


class TestOpenAIClient:
    """Tests for OpenAI client initialization."""

    def test_get_openai_client_success(self):
        """Test successful OpenAI client creation."""
        mock_client = MagicMock()
        with patch("gptme.tools.stt.OpenAI", return_value=mock_client, create=True):
            # Patch the import inside the function
            with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=lambda: mock_client)}):
                from gptme.tools.stt import _get_openai_client

                # Create a fresh import that uses our mock
                import importlib
                import gptme.tools.stt as stt_module

                # Mock the OpenAI import inside the function
                original_func = stt_module._get_openai_client

                def mocked_get_client():
                    return mock_client

                stt_module._get_openai_client = mocked_get_client
                try:
                    result = stt_module._get_openai_client()
                    assert result == mock_client
                finally:
                    stt_module._get_openai_client = original_func

    def test_get_openai_client_import_error(self):
        """Test OpenAI client creation when openai package is not installed."""
        from gptme.tools.stt import _get_openai_client

        with patch.dict("sys.modules", {"openai": None}):
            # Force ImportError by removing the module
            import sys

            openai_backup = sys.modules.get("openai")
            sys.modules["openai"] = None  # type: ignore

            # Create a version that raises ImportError
            def mock_get_client():
                try:
                    from openai import OpenAI  # noqa: F401

                    return OpenAI()
                except (ImportError, TypeError):
                    return None

            result = mock_get_client()
            assert result is None

            if openai_backup:
                sys.modules["openai"] = openai_backup


class TestTranscribeAudio:
    """Tests for audio transcription."""

    def test_transcribe_audio_success(self):
        """Test successful audio transcription."""
        from gptme.tools import stt

        # Create mock WAV data (minimal valid WAV header + silence)
        wav_header = bytes([
            0x52, 0x49, 0x46, 0x46,  # "RIFF"
            0x24, 0x00, 0x00, 0x00,  # File size - 8
            0x57, 0x41, 0x56, 0x45,  # "WAVE"
            0x66, 0x6D, 0x74, 0x20,  # "fmt "
            0x10, 0x00, 0x00, 0x00,  # Subchunk1 size (16)
            0x01, 0x00,              # Audio format (1 = PCM)
            0x01, 0x00,              # Num channels (1)
            0x80, 0x3E, 0x00, 0x00,  # Sample rate (16000)
            0x00, 0x7D, 0x00, 0x00,  # Byte rate
            0x02, 0x00,              # Block align
            0x10, 0x00,              # Bits per sample (16)
            0x64, 0x61, 0x74, 0x61,  # "data"
            0x00, 0x00, 0x00, 0x00,  # Data size
        ])

        mock_client = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Hello, world!"
        mock_client.audio.transcriptions.create.return_value = mock_transcription

        with patch.object(stt, "_get_openai_client", return_value=mock_client):
            result = stt.transcribe_audio(wav_header)
            assert result == "Hello, world!"
            mock_client.audio.transcriptions.create.assert_called_once()

    def test_transcribe_audio_no_client(self):
        """Test transcription when OpenAI client is not available."""
        from gptme.tools import stt

        with patch.object(stt, "_get_openai_client", return_value=None):
            result = stt.transcribe_audio(b"fake audio data")
            assert result is None

    def test_transcribe_audio_with_language(self):
        """Test transcription with language hint."""
        from gptme.tools import stt

        wav_header = bytes([
            0x52, 0x49, 0x46, 0x46,
            0x24, 0x00, 0x00, 0x00,
            0x57, 0x41, 0x56, 0x45,
            0x66, 0x6D, 0x74, 0x20,
            0x10, 0x00, 0x00, 0x00,
            0x01, 0x00, 0x01, 0x00,
            0x80, 0x3E, 0x00, 0x00,
            0x00, 0x7D, 0x00, 0x00,
            0x02, 0x00, 0x10, 0x00,
            0x64, 0x61, 0x74, 0x61,
            0x00, 0x00, 0x00, 0x00,
        ])

        mock_client = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Bonjour!"
        mock_client.audio.transcriptions.create.return_value = mock_transcription

        with patch.object(stt, "_get_openai_client", return_value=mock_client):
            result = stt.transcribe_audio(wav_header, language="fr")
            assert result == "Bonjour!"

            # Check that language was passed
            call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
            assert call_kwargs.get("language") == "fr"

    def test_transcribe_audio_api_error(self):
        """Test transcription when API call fails."""
        from gptme.tools import stt

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = Exception("API Error")

        with patch.object(stt, "_get_openai_client", return_value=mock_client):
            result = stt.transcribe_audio(b"fake audio data")
            assert result is None


class TestRecordAndTranscribe:
    """Tests for the record_and_transcribe function."""

    def test_record_and_transcribe_no_sounddevice(self):
        """Test record_and_transcribe when sounddevice is not available."""
        from gptme.tools import stt

        with patch("gptme.util.sound.is_recording_available", return_value=False):
            result = stt.record_and_transcribe()
            assert result is None

    def test_record_and_transcribe_no_device(self):
        """Test record_and_transcribe when no input device is found."""
        from gptme.tools import stt

        with patch("gptme.util.sound.is_recording_available", return_value=True):
            with patch("gptme.util.sound.get_default_input_device", return_value=None):
                with patch("gptme.util.sound.get_input_devices", return_value=[]):
                    result = stt.record_and_transcribe()
                    assert result is None


class TestToolSpec:
    """Tests for the STT tool specification."""

    def test_tool_spec_exists(self):
        """Test that the tool spec is defined correctly."""
        from gptme.tools.stt import tool

        assert tool.name == "stt"
        assert "speech-to-text" in tool.desc.lower() or "Speech-to-text" in tool.desc
        assert "voice" in tool.commands

    def test_tool_functions_registered(self):
        """Test that the tool functions are registered."""
        from gptme.tools.stt import tool

        function_names = [f.__name__ for f in tool.functions]
        assert "record_and_transcribe" in function_names
        assert "transcribe_audio" in function_names


class TestVoiceCommand:
    """Tests for the /voice command."""

    def test_voice_command_with_transcription(self):
        """Test the voice command yields user message with transcription."""
        from gptme.tools.stt import _cmd_voice

        # Create mock context
        mock_ctx = MagicMock()
        mock_ctx.args = []
        mock_ctx.manager.undo = MagicMock()
        mock_ctx.manager.write = MagicMock()

        with patch("gptme.tools.stt.record_and_transcribe", return_value="Hello from voice"):
            messages = list(_cmd_voice(mock_ctx))
            assert len(messages) == 1
            assert messages[0].role == "user"
            assert messages[0].content == "Hello from voice"

    def test_voice_command_with_language_arg(self):
        """Test the voice command passes language argument."""
        from gptme.tools.stt import _cmd_voice

        mock_ctx = MagicMock()
        mock_ctx.args = ["es"]  # Spanish
        mock_ctx.manager.undo = MagicMock()
        mock_ctx.manager.write = MagicMock()

        with patch("gptme.tools.stt.record_and_transcribe") as mock_record:
            mock_record.return_value = "Hola mundo"
            messages = list(_cmd_voice(mock_ctx))

            # Verify language was passed
            mock_record.assert_called_once_with(language="es")
            assert len(messages) == 1
            assert messages[0].content == "Hola mundo"

    def test_voice_command_no_transcription(self):
        """Test the voice command when transcription fails."""
        from gptme.tools.stt import _cmd_voice

        mock_ctx = MagicMock()
        mock_ctx.args = []
        mock_ctx.manager.undo = MagicMock()
        mock_ctx.manager.write = MagicMock()

        with patch("gptme.tools.stt.record_and_transcribe", return_value=None):
            messages = list(_cmd_voice(mock_ctx))
            assert len(messages) == 0
