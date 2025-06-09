#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "kokoro>=0.3.4",
#   "scipy>=1.11.0",
#   "soundfile>=0.12.1",
#   "numpy",
# ]
# ///
"""
Kokoro TTS backend implementation.
"""

import io
import logging
import shutil

import numpy as np
import scipy.io.wavfile as wavfile
from kokoro import KPipeline

log = logging.getLogger(__name__)


class KokoroTTSBackend:
    """Kokoro TTS backend implementation."""

    def __init__(self, lang_code: str = "a", voice: str = "af_heart"):
        self.lang_code = lang_code
        self.default_voice = voice
        self.pipeline = None
        self._check_espeak()

    def _check_espeak(self):
        """Check if espeak/espeak-ng is installed."""
        if not any([shutil.which("espeak"), shutil.which("espeak-ng")]):
            raise RuntimeError(
                "Failed to find `espeak` or `espeak-ng`. Try to install it using 'sudo apt-get install espeak-ng' or equivalent"
            ) from None

    def get_language_codes(self) -> dict[str, str]:
        """Get supported language codes and their descriptions."""
        return {
            "a": "American English",
            "b": "British English",
            "j": "Japanese",
            "z": "Mandarin Chinese",
            "e": "Spanish",
            "f": "French",
            "h": "Hindi",
            "i": "Italian",
            "p": "Brazilian Portuguese",
        }

    def list_voices(self, lang_code: str = None) -> list[str]:
        """List all available voices for the given language."""
        lang = lang_code or self.lang_code

        # American English voices (most complete set)
        if lang == "a":
            return [
                "af_heart",
                "af_alloy",
                "af_aoede",
                "af_bella",
                "af_jessica",
                "af_kore",
                "af_nicole",
                "af_nova",
                "af_river",
                "af_sarah",
                "af_sky",
                "am_adam",
                "am_echo",
                "am_eric",
                "am_fenrir",
                "am_liam",
                "am_michael",
                "am_onyx",
                "am_puck",
                "am_santa",
            ]
        # British English voices
        elif lang == "b":
            return [
                "bf_alice",
                "bf_emma",
                "bf_isabella",
                "bf_lily",
                "bm_daniel",
                "bm_fable",
                "bm_george",
                "bm_lewis",
            ]
        # Japanese voices
        elif lang == "j":
            return ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"]
        # Mandarin Chinese voices
        elif lang == "z":
            return [
                "zf_xiaobei",
                "zf_xiaoni",
                "zf_xiaoxiao",
                "zf_xiaoyi",
                "zm_yunjian",
                "zm_yunxi",
                "zm_yunxia",
                "zm_yunyang",
            ]
        # Spanish voices
        elif lang == "e":
            return ["ef_dora", "em_alex", "em_santa"]
        # French voices
        elif lang == "f":
            return ["ff_siwis"]
        # Hindi voices
        elif lang == "h":
            return ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"]
        # Italian voices
        elif lang == "i":
            return ["if_sara", "im_nicola"]
        # Brazilian Portuguese voices
        elif lang == "p":
            return ["pf_dora", "pm_alex", "pm_santa"]

        return ["af_heart"]  # Default fallback

    def initialize(self, voice: str = None) -> None:
        """Initialize the Kokoro TTS pipeline."""
        try:
            # Use specified voice or default
            voice_name = voice or self.default_voice

            # Initialize the pipeline with language
            self.pipeline = KPipeline(lang_code=self.lang_code)

            # Verify voice exists
            available_voices = self.list_voices()
            if voice_name not in available_voices:
                raise ValueError(
                    f"Voice {voice_name} not found. Available voices: {available_voices}"
                )

            self.default_voice = voice_name
            log.info(
                f"Kokoro pipeline initialized (lang: {self.lang_code}, voice: {voice_name})"
            )

        except Exception as e:
            log.error(f"Failed to initialize Kokoro pipeline: {e}")
            raise

    def strip_silence(
        self,
        audio_data: np.ndarray,
        threshold: float = 0.01,
        min_silence_duration: int = 1000,
    ) -> np.ndarray:
        """Strip silence from the beginning and end of audio data."""
        # Convert to absolute values
        abs_audio = np.abs(audio_data)

        # Find indices where audio is above threshold
        mask = abs_audio > threshold

        # Find first and last non-silent points
        non_silent = np.where(mask)[0]
        if len(non_silent) == 0:
            return audio_data

        start = max(0, non_silent[0] - min_silence_duration)
        end = min(len(audio_data), non_silent[-1] + min_silence_duration)

        return audio_data[start:end]

    def synthesize(
        self, text: str, voice: str = None, speed: float = 1.0
    ) -> io.BytesIO:
        """Convert text to speech and return audio buffer."""
        if not self.pipeline:
            raise RuntimeError("Pipeline not initialized. Call initialize() first.")

        # Handle voice selection
        current_voice = voice or self.default_voice
        if current_voice not in self.list_voices():
            raise ValueError(f"Voice {current_voice} not found")

        try:
            log.info(
                f"Generating audio with Kokoro (voice: {current_voice}, speed: {speed}x)"
            )

            # Generate audio using KPipeline
            audio_segments = []
            for _, _, audio in self.pipeline(text, voice=current_voice, speed=speed):
                audio_segments.append(audio)

            # Concatenate all audio segments
            if not audio_segments:
                raise ValueError("No audio generated")

            audio = np.concatenate(audio_segments)

            # Strip silence from audio
            audio = self.strip_silence(audio)

            # Convert audio to proper format for WAV
            # Normalize to [-1, 1] range first
            if np.max(np.abs(audio)) > 1.0:
                audio = audio / np.max(np.abs(audio))

            # Convert to 16-bit integer format (standard for WAV files)
            audio_int16 = (audio * 32767).astype(np.int16)

            # Convert to WAV format
            buffer = io.BytesIO()
            wavfile.write(buffer, 24000, audio_int16)
            buffer.seek(0)

            return buffer

        except Exception as e:
            log.error(f"Failed to generate speech with Kokoro: {e}")
            raise

    def get_info(self) -> dict:
        """Get backend information."""
        try:
            import kokoro

            version = kokoro.__version__
        except:
            version = "unknown"

        return {
            "name": "kokoro",
            "version": version,
            "language": {
                "default": self.lang_code,
                "name": self.get_language_codes().get(self.lang_code, "Unknown"),
                "supported": self.get_language_codes(),
            },
            "voice": {
                "default": self.default_voice,
                "available": self.list_voices(),
            },
        }
