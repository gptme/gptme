#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "scipy>=1.11.0",
#   "soundfile>=0.12.1",
#   "numpy",
# ]
# ///
"""
Generate a pleasant bell/ding sound for gptme notifications.
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def generate_bell_sound(
    duration: float = 1.5,
    sample_rate: int = 44100,
    fundamental_freq: float = 800.0,
    volume: float = 0.3,
) -> np.ndarray:
    """
    Generate a pleasant bell sound using multiple harmonics with exponential decay.

    Args:
        duration: Length of the sound in seconds
        sample_rate: Audio sample rate in Hz
        fundamental_freq: Base frequency of the bell in Hz
        volume: Volume level (0.0 to 1.0)

    Returns:
        Audio samples as numpy array
    """
    # Create time array
    t = np.linspace(0, duration, int(sample_rate * duration))

    # Bell harmonics (frequency ratios based on real bell acoustics)
    harmonics = [
        (1.0, 1.0),  # Fundamental
        (2.76, 0.6),  # First overtone
        (5.40, 0.4),  # Second overtone
        (8.93, 0.25),  # Third overtone
        (13.34, 0.15),  # Fourth overtone
        (18.64, 0.1),  # Fifth overtone
    ]

    # Generate the bell sound
    bell_sound = np.zeros_like(t)

    for freq_ratio, amplitude in harmonics:
        freq = fundamental_freq * freq_ratio

        # Create the sine wave
        sine_wave = np.sin(2 * np.pi * freq * t)

        # Apply exponential decay (different decay rates for different harmonics)
        decay_rate = 3.0 + freq_ratio * 0.5  # Higher frequencies decay faster
        envelope = np.exp(-decay_rate * t)

        # Add some subtle frequency modulation for richness
        modulation = 1 + 0.02 * np.sin(2 * np.pi * 5 * t) * envelope

        # Combine and add to the bell sound
        bell_sound += amplitude * sine_wave * envelope * modulation

    # Apply a gentle attack envelope to avoid clicking
    attack_samples = int(0.01 * sample_rate)  # 10ms attack
    attack_envelope = np.ones_like(t)
    attack_envelope[:attack_samples] = np.linspace(0, 1, attack_samples)

    bell_sound *= attack_envelope

    # Normalize and apply volume
    bell_sound = bell_sound / np.max(np.abs(bell_sound))
    bell_sound *= volume

    return bell_sound.astype(np.float32)


def save_bell_sound(output_path: Path, **kwargs) -> None:
    """Generate and save a bell sound to a file."""
    bell_sound = generate_bell_sound(**kwargs)

    # Save as WAV file
    sf.write(output_path, bell_sound, 44100)
    print(f"Bell sound saved to: {output_path}")


def play_bell_sound(audio_data: np.ndarray, sample_rate: int = 44100) -> None:
    """Play the bell sound using the system's default audio player."""
    # Create a temporary file
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        # Save to temporary file
        sf.write(tmp_path, audio_data, sample_rate)

        # Try to play using different system commands
        play_commands = [
            ["afplay", tmp_path],  # macOS
            ["aplay", tmp_path],  # Linux (ALSA)
            ["paplay", tmp_path],  # Linux (PulseAudio)
            ["play", tmp_path],  # SoX
        ]

        for cmd in play_commands:
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                    return
                except subprocess.CalledProcessError:
                    continue

        print(
            "Could not find a suitable audio player. Audio saved to temporary file:",
            tmp_path,
        )

    finally:
        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Generate a pleasant bell sound")
    parser.add_argument(
        "-o", "--output", type=Path, help="Output file path (default: bell_sound.wav)"
    )
    parser.add_argument(
        "-p", "--play", action="store_true", help="Play the sound immediately"
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=1.5,
        help="Duration in seconds (default: 1.5)",
    )
    parser.add_argument(
        "-f",
        "--frequency",
        type=float,
        default=800.0,
        help="Fundamental frequency in Hz (default: 800.0)",
    )
    parser.add_argument(
        "-v",
        "--volume",
        type=float,
        default=0.3,
        help="Volume level 0.0-1.0 (default: 0.3)",
    )

    args = parser.parse_args()

    # Generate the bell sound
    bell_sound = generate_bell_sound(
        duration=args.duration, fundamental_freq=args.frequency, volume=args.volume
    )

    # Save to file if requested
    if args.output:
        save_bell_sound(
            args.output,
            duration=args.duration,
            fundamental_freq=args.frequency,
            volume=args.volume,
        )

    # Play the sound if requested
    if args.play:
        play_bell_sound(bell_sound)

    # Default behavior: save to bell_sound.wav
    if not args.output and not args.play:
        default_output = Path("bell_sound.wav")
        save_bell_sound(
            default_output,
            duration=args.duration,
            fundamental_freq=args.frequency,
            volume=args.volume,
        )


if __name__ == "__main__":
    main()
