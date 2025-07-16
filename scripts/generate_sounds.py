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
Generate various sounds for gptme tool notifications.
"""

import argparse
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

SoundGenerator = Callable[[], np.ndarray]


def generate_bell_sound(
    duration: float = 1.5,
    sample_rate: int = 44100,
    fundamental_freq: float = 800.0,
    volume: float = 0.3,
) -> np.ndarray:
    """Generate a pleasant bell sound using multiple harmonics with exponential decay."""
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

    bell_sound = np.zeros_like(t)

    for freq_ratio, amplitude in harmonics:
        freq = fundamental_freq * freq_ratio
        sine_wave = np.sin(2 * np.pi * freq * t)
        decay_rate = 3.0 + freq_ratio * 0.5
        envelope = np.exp(-decay_rate * t)
        modulation = 1 + 0.02 * np.sin(2 * np.pi * 5 * t) * envelope
        bell_sound += amplitude * sine_wave * envelope * modulation

    # Attack envelope
    attack_samples = int(0.01 * sample_rate)
    attack_envelope = np.ones_like(t)
    attack_envelope[:attack_samples] = np.linspace(0, 1, attack_samples)

    bell_sound *= attack_envelope
    bell_sound = bell_sound / np.max(np.abs(bell_sound))
    bell_sound *= volume

    return bell_sound.astype(np.float32)


def generate_sawing_sound(
    duration: float = 0.5,
    sample_rate: int = 44100,
    volume: float = 0.2,
) -> np.ndarray:
    """Generate a gentle whir sound for general tool use."""
    t = np.linspace(0, duration, int(sample_rate * duration))

    # Gentle whir: soft oscillating tone
    base_freq = 300.0
    modulation_freq = 8.0

    # Create oscillating frequency
    freq_modulation = 1 + 0.3 * np.sin(2 * np.pi * modulation_freq * t)
    whir_sound = np.sin(2 * np.pi * base_freq * freq_modulation * t)

    # Add subtle harmonics
    whir_sound += 0.4 * np.sin(2 * np.pi * base_freq * 2 * freq_modulation * t)
    whir_sound += 0.2 * np.sin(2 * np.pi * base_freq * 3 * freq_modulation * t)

    # Smooth envelope
    envelope = np.sin(np.pi * t / duration) * 0.8 + 0.2
    whir_sound *= envelope

    # Final envelope
    fade_samples = int(0.05 * sample_rate)
    final_envelope = np.ones_like(t)
    final_envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    final_envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    whir_sound *= final_envelope
    whir_sound = whir_sound / np.max(np.abs(whir_sound))
    whir_sound *= volume

    return whir_sound.astype(np.float32)


def generate_drilling_sound(
    duration: float = 0.4,
    sample_rate: int = 44100,
    volume: float = 0.25,
) -> np.ndarray:
    """Generate a soft buzz sound for alternative general tool use."""
    t = np.linspace(0, duration, int(sample_rate * duration))

    # Soft buzz: steady tone with slight vibrato
    buzz_freq = 400.0
    vibrato_freq = 6.0
    vibrato_depth = 0.1

    # Create vibrato
    vibrato = 1 + vibrato_depth * np.sin(2 * np.pi * vibrato_freq * t)
    buzz_sound = np.sin(2 * np.pi * buzz_freq * vibrato * t)

    # Add harmonics for warmth
    buzz_sound += 0.3 * np.sin(2 * np.pi * buzz_freq * 2 * vibrato * t)
    buzz_sound += 0.1 * np.sin(2 * np.pi * buzz_freq * 3 * vibrato * t)

    # Smooth envelope
    envelope = np.sin(np.pi * t / duration) * 0.9 + 0.1
    buzz_sound *= envelope

    # Final envelope
    fade_samples = int(0.03 * sample_rate)
    final_envelope = np.ones_like(t)
    final_envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    final_envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    buzz_sound *= final_envelope
    buzz_sound = buzz_sound / np.max(np.abs(buzz_sound))
    buzz_sound *= volume

    return buzz_sound.astype(np.float32)


def generate_page_turn_sound(
    duration: float = 0.6,
    sample_rate: int = 44100,
    volume: float = 0.25,
) -> np.ndarray:
    """Generate a soft whoosh sound for read operations."""
    t = np.linspace(0, duration, int(sample_rate * duration))

    # Soft whoosh: frequency sweep from low to high
    start_freq = 200.0
    end_freq = 800.0

    # Create frequency sweep
    freq_sweep = start_freq + (end_freq - start_freq) * (t / duration)
    whoosh_sound = np.sin(2 * np.pi * freq_sweep * t)

    # Add subtle harmonics
    whoosh_sound += 0.3 * np.sin(2 * np.pi * freq_sweep * 2 * t)

    # Smooth envelope that peaks in the middle
    envelope = np.sin(np.pi * t / duration) * np.exp(-2 * t)
    whoosh_sound *= envelope

    # Final envelope
    fade_samples = int(0.05 * sample_rate)
    final_envelope = np.ones_like(t)
    final_envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    final_envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    whoosh_sound *= final_envelope
    whoosh_sound = whoosh_sound / np.max(np.abs(whoosh_sound))
    whoosh_sound *= volume

    return whoosh_sound.astype(np.float32)


def generate_seashell_click_sound(
    duration: float = 0.3,
    sample_rate: int = 44100,
    volume: float = 0.3,
) -> np.ndarray:
    """Generate a pleasant click sound for shell commands."""
    t = np.linspace(0, duration, int(sample_rate * duration))

    # Pleasant click: short, crisp tone with harmonics
    click_freq = 1000.0
    click_sound = np.sin(2 * np.pi * click_freq * t)

    # Add harmonics for pleasant timbre
    click_sound += 0.5 * np.sin(2 * np.pi * click_freq * 2 * t)
    click_sound += 0.25 * np.sin(2 * np.pi * click_freq * 3 * t)

    # Quick exponential decay for crisp click
    click_envelope = np.exp(-12 * t)
    click_sound *= click_envelope

    # Final envelope
    fade_samples = int(0.005 * sample_rate)
    envelope = np.ones_like(t)
    envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    click_sound *= envelope
    click_sound = click_sound / np.max(np.abs(click_sound))
    click_sound *= volume

    return click_sound.astype(np.float32)


def generate_camera_shutter_sound(
    duration: float = 0.4,
    sample_rate: int = 44100,
    volume: float = 0.3,
) -> np.ndarray:
    """Generate a bright snap sound for screenshot operations."""
    t = np.linspace(0, duration, int(sample_rate * duration))

    # Bright snap: two quick tones
    snap_freq1 = 1200.0
    snap_freq2 = 1800.0

    # First snap (quick and bright)
    snap1_duration = 0.1
    snap1_mask = t < snap1_duration
    snap1_t = t[snap1_mask]
    snap1 = np.sin(2 * np.pi * snap_freq1 * snap1_t) * np.exp(-15 * snap1_t)

    # Second snap (slightly different pitch)
    snap2_start = 0.15
    snap2_duration = 0.1
    snap2_mask = (t >= snap2_start) & (t < snap2_start + snap2_duration)
    snap2_t = t[snap2_mask] - snap2_start
    snap2 = np.sin(2 * np.pi * snap_freq2 * snap2_t) * np.exp(-15 * snap2_t)

    # Combine snaps
    snap_sound = np.zeros_like(t)
    snap_sound[snap1_mask] += snap1
    snap_sound[snap2_mask] += snap2

    # Final envelope
    fade_samples = int(0.01 * sample_rate)
    envelope = np.ones_like(t)
    envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    snap_sound *= envelope
    snap_sound = snap_sound / np.max(np.abs(snap_sound))
    snap_sound *= volume

    return snap_sound.astype(np.float32)


def generate_file_write_sound(
    duration: float = 0.5,
    sample_rate: int = 44100,
    volume: float = 0.15,
) -> np.ndarray:
    """Generate a gentle scribble sound for file write operations."""
    t = np.linspace(0, duration, int(sample_rate * duration))

    # Gentle scribble: very subtle frequency wobble
    base_freq = 350.0  # Lower frequency for gentler feel
    wobble_freq = 8.0  # Slower wobble
    wobble_depth = 0.2  # Less wobble depth

    # Create gentle frequency wobble
    freq_wobble = base_freq * (1 + wobble_depth * np.sin(2 * np.pi * wobble_freq * t))
    scribble_sound = np.sin(2 * np.pi * freq_wobble * t)

    # Add very subtle harmonics
    scribble_sound += 0.15 * np.sin(2 * np.pi * freq_wobble * 1.5 * t)
    scribble_sound += 0.1 * np.sin(2 * np.pi * freq_wobble * 2.5 * t)

    # Minimal texture noise
    texture_noise = np.random.normal(0, 0.02, len(t))
    scribble_sound += texture_noise

    # Very gentle envelope that fades in and out smoothly
    envelope = np.sin(np.pi * t / duration) * 0.8 + 0.2
    scribble_sound *= envelope

    # Longer fade for smoother sound
    fade_samples = int(0.08 * sample_rate)
    final_envelope = np.ones_like(t)
    final_envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    final_envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    scribble_sound *= final_envelope
    scribble_sound = scribble_sound / np.max(np.abs(scribble_sound))
    scribble_sound *= volume

    return scribble_sound.astype(np.float32)


def save_bell_sound(output_path: Path, **kwargs) -> None:
    """Generate and save a bell sound to a file."""
    bell_sound = generate_bell_sound(**kwargs)
    sf.write(output_path, bell_sound, 44100)
    print(f"Bell sound saved to: {output_path}")


def play_sound(audio_data: np.ndarray, sample_rate: int = 44100) -> None:
    """Play the sound using the system's default audio player."""
    # Create a temporary file
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


def generate_all_sounds(output_dir: Path):
    """Generate all tool sounds and save them to the output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    sounds: dict[str, SoundGenerator] = {
        "bell.wav": generate_bell_sound,
        "sawing.wav": generate_sawing_sound,
        "drilling.wav": generate_drilling_sound,
        "page_turn.wav": generate_page_turn_sound,
        "seashell_click.wav": generate_seashell_click_sound,
        "camera_shutter.wav": generate_camera_shutter_sound,
        "file_write.wav": generate_file_write_sound,
    }

    for filename, generator in sounds.items():
        sound_data = generator()
        output_path = output_dir / filename
        sf.write(output_path, sound_data, 44100)
        print(f"Generated {filename}")


SRC_DIR = Path(__file__).parent.resolve()


def main():
    parser = argparse.ArgumentParser(description="Generate tool sounds for gptme")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output directory or file path (default: $GPTME_ROOT/media for --sound=all, or specific filename for individual sounds)",
    )
    parser.add_argument(
        "--sound",
        choices=[
            "bell",
            "sawing",
            "drilling",
            "page_turn",
            "seashell_click",
            "camera_shutter",
            "file_write",
            "all",
        ],
        default="all",
        help="Which sound to generate (default: all)",
    )
    parser.add_argument(
        "-p", "--play", action="store_true", help="Play the generated sound"
    )

    # Bell sound customization options
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=1.5,
        help="Duration in seconds for bell sound (default: 1.5)",
    )
    parser.add_argument(
        "-f",
        "--frequency",
        type=float,
        default=800.0,
        help="Fundamental frequency in Hz for bell sound (default: 800.0)",
    )
    parser.add_argument(
        "-v",
        "--volume",
        type=float,
        default=0.3,
        help="Volume level 0.0-1.0 for bell sound (default: 0.3)",
    )

    args = parser.parse_args()

    if args.sound == "all":
        output_dir = args.output or (SRC_DIR / ".." / "media").resolve()
        generate_all_sounds(output_dir)
    else:
        generators: dict[str, SoundGenerator] = {
            "bell": lambda: generate_bell_sound(
                duration=args.duration,
                fundamental_freq=args.frequency,
                volume=args.volume,
            ),
            "sawing": generate_sawing_sound,
            "drilling": generate_drilling_sound,
            "page_turn": generate_page_turn_sound,
            "seashell_click": generate_seashell_click_sound,
            "camera_shutter": generate_camera_shutter_sound,
            "file_write": generate_file_write_sound,
        }

        sound_data = generators[args.sound]()

        if args.output:
            output_path = args.output
        else:
            output_dir = (SRC_DIR / ".." / "media").resolve()
            output_path = output_dir / f"{args.sound}.wav"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output_path, sound_data, 44100)
        print(f"Generated {args.sound}.wav at {output_path}")

        if args.play:
            play_sound(sound_data)


if __name__ == "__main__":
    main()
