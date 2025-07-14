#!/usr/bin/env python3
"""Simple test script to verify tool sounds work."""

import os
import sys
import time
from pathlib import Path

# Add the gptme directory to the path so we can import the modules
sys.path.insert(0, str(Path(__file__).parent))


def test_tool_sounds():
    """Test tool sounds functionality."""
    print("Testing gptme tool sounds feature...")

    # Set the environment variable to enable tool sounds
    os.environ["GPTME_TOOL_SOUNDS"] = "true"

    from gptme.util.sound import (
        play_tool_sound,
        get_tool_sound_for_tool,
        is_audio_available,
    )

    if not is_audio_available():
        print("⚠️  Audio not available - sounds will be skipped")
        return

    print("✅ Audio available")
    print()

    # Test individual sounds
    print("Testing individual sounds:")
    sounds = [
        ("bell", "User attention needed"),
        ("sawing", "General tool use"),
        ("drilling", "Alternative general tool use"),
        ("page_turn", "Read operations"),
        ("seashell_click", "Shell commands"),
        ("camera_shutter", "Screenshot operations"),
        ("file_write", "File write operations"),
    ]

    for sound, description in sounds:
        print(f"  🔊 {sound}: {description}")
        if sound == "bell":
            from gptme.util.sound import play_ding

            play_ding()
        else:
            play_tool_sound(sound)
        time.sleep(1.5)

    print()
    print("Testing tool sound mappings:")
    tools = [
        "shell",
        "read",
        "screenshot",
        "ipython",
        "save",
        "append",
        "patch",
        "morph",
        "browser",
        "gh",
    ]

    for tool in tools:
        sound_type = get_tool_sound_for_tool(tool)
        print(f"  🛠️  {tool:<10} -> {sound_type or 'None'}")
        if sound_type:
            play_tool_sound(sound_type)
            time.sleep(1.0)

    print()
    print("✅ Tool sounds test completed!")
    print()
    print("To use tool sounds in gptme:")
    print("  export GPTME_TOOL_SOUNDS=true")
    print("  gptme 'list the current directory'")


if __name__ == "__main__":
    test_tool_sounds()
